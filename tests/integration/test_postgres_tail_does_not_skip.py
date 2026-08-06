"""Against a real PostgreSQL: the position cursor skips, the watermark does not.

This is the one test in the tail work that a fake cannot stand in for. The
defect is MVCC behaviour -- a `BIGSERIAL` is handed out when the row is inserted
and the row becomes visible when the transaction commits, and those are two
different moments -- so an in-memory stand-in that interprets SQL statements
would agree with whichever implementation wrote it.

So it runs against a server, and it **reproduces the loss first**. A test that
only asserts the fix passes just as well against a store that never had the
problem, which would make it worth nothing here.

Opt-in, like the other `live` tests: set `HANGAR_TEST_POSTGRES_DSN`. Locally,

    podman run -d --name ha-pg -e POSTGRES_PASSWORD=hangar -e POSTGRES_USER=hangar \\
        -e POSTGRES_DB=hangar -p 55432:5432 docker.io/library/postgres:16-alpine
    HANGAR_TEST_POSTGRES_DSN='host=127.0.0.1 port=55432 user=hangar password=hangar dbname=hangar' \\
        pytest tests/integration/test_postgres_tail_does_not_skip.py -m live
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import threading
import time
from typing import Any

import pytest

from mcp_hangar.domain.contracts.event_store import BEGINNING
from mcp_hangar.domain.events import McpServerStarted
from mcp_hangar.infrastructure.persistence.backends.postgresql.event_store import PostgresEventStore

pytestmark = pytest.mark.live

DSN = os.environ.get("HANGAR_TEST_POSTGRES_DSN", "")

psycopg2 = pytest.importorskip("psycopg2", reason="the postgres extra is not installed")

if not DSN:
    pytest.skip("HANGAR_TEST_POSTGRES_DSN is not set", allow_module_level=True)


class _DirectFactory:
    """One connection per call, straight from the DSN.

    Not the pooling factory the backend ships: this test needs several
    connections open at once, with one of them holding a transaction open on
    purpose, and a pool would either lend the same connection twice or block.
    """

    @contextmanager
    def get_connection(self):
        conn = psycopg2.connect(DSN)
        try:
            yield conn
        finally:
            conn.close()


def _event(server_id: str) -> McpServerStarted:
    return McpServerStarted(mcp_server_id=server_id, mode="subprocess", tools_count=1, startup_duration_ms=1.0)


@pytest.fixture
def store():
    prefix = "tailtest_"
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {prefix}events, {prefix}streams, {prefix}snapshots")
        conn.commit()
    store = PostgresEventStore(_DirectFactory(), table_prefix=prefix)
    store.initialize()
    yield store
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {prefix}events, {prefix}streams, {prefix}snapshots")
        conn.commit()


@contextmanager
def _appending_slowly(store: PostgresEventStore, server_id: str) -> Any:
    """Append inside a transaction that stays open until the block exits.

    This is the shape the race needs and the only part that is contrived: the
    row takes its position now and becomes visible later. In production the same
    window opens on its own -- it is just short.
    """
    started = threading.Event()
    release = threading.Event()

    def run() -> None:
        conn = psycopg2.connect(DSN)
        cur = conn.cursor()
        event_type, data = store._serializer.serialize(_event(server_id))
        cur.execute(
            f"INSERT INTO {store._streams_table} (stream_id, version, created_at, updated_at) "
            "VALUES (%s, 0, 'now', 'now')",
            (f"mcp_server:{server_id}",),
        )
        cur.execute(
            f"INSERT INTO {store._events_table} "
            "(stream_id, stream_version, event_type, data, created_at) VALUES (%s, 0, %s, %s, 'now')",
            (f"mcp_server:{server_id}", event_type, data),
        )
        started.set()
        release.wait(timeout=30)
        conn.commit()
        conn.close()

    worker = threading.Thread(target=run)
    worker.start()
    started.wait(timeout=10)
    try:
        yield
    finally:
        release.set()
        worker.join(timeout=30)
        # Let the commit become visible to a new snapshot.
        time.sleep(0.1)


class TestTheRaceIsReal:
    def test_a_position_cursor_loses_the_slower_transaction(self, store) -> None:
        # First: prove the defect, or the fix below proves nothing. `slow` takes
        # position 1 and commits second; `fast` takes 2 and commits first. A
        # cursor that advanced to 2 never comes back for 1.
        with _appending_slowly(store, "slow"):
            store.append("mcp_server:fast", [_event("fast")], expected_version=-1)
            position = max((p for p, _s, _e in store.read_all(from_position=0)), default=0)

        seen_after = [stream for _p, stream, _e in store.read_all(from_position=position)]

        assert seen_after == [], "the slow event arrived where a position cursor would have found it"
        everything = [stream for _p, stream, _e in store.read_all(from_position=0)]
        assert "mcp_server:slow" in everything, "the slow event did commit -- it was skipped, not missing"


class TestTheWatermarkCursorDoesNot:
    def test_nothing_is_skipped_when_a_transaction_commits_late(self, store) -> None:
        with _appending_slowly(store, "slow"):
            store.append("mcp_server:fast", [_event("fast")], expected_version=-1)
            batch, cursor = store.read_since(BEGINNING)

        later, _cursor = store.read_since(cursor)

        delivered = [stream for stream, _e in batch] + [stream for stream, _e in later]
        assert sorted(delivered) == ["mcp_server:fast", "mcp_server:slow"]

    def test_it_holds_an_event_back_rather_than_delivering_it_early(self, store) -> None:
        # The trade, asserted rather than assumed: while a transaction is open
        # the tail waits. Delivery lags; it does not skip. If this ever started
        # returning the fast event immediately, the cursor would have advanced
        # past the slow one and the test above would be the thing that broke.
        with _appending_slowly(store, "slow"):
            store.append("mcp_server:fast", [_event("fast")], expected_version=-1)
            batch, _cursor = store.read_since(BEGINNING)

        assert batch == []

    def test_delivery_is_once_each_across_polls(self, store) -> None:
        store.append("mcp_server:a", [_event("a")], expected_version=-1)
        store.append("mcp_server:b", [_event("b")], expected_version=-1)

        first, cursor = store.read_since(BEGINNING)
        second, cursor = store.read_since(cursor)
        third, _cursor = store.read_since(cursor)

        assert sorted(stream for stream, _e in first) == ["mcp_server:a", "mcp_server:b"]
        assert second == [] and third == []

    def test_a_short_batch_resumes_inside_the_transaction_it_stopped_in(self, store) -> None:
        # One append, three events, one transaction. A limit that cuts it in
        # half must not resume at the horizon, which would skip the rest.
        store.append("mcp_server:a", [_event("a"), _event("a"), _event("a")], expected_version=-1)

        first, cursor = store.read_since(BEGINNING, limit=2)
        second, _cursor = store.read_since(cursor, limit=2)

        assert len(first) == 2
        assert len(second) == 1

    def test_the_head_skips_what_is_already_there(self, store) -> None:
        store.append("mcp_server:old", [_event("old")], expected_version=-1)

        head = store.tail_head()
        store.append("mcp_server:new", [_event("new")], expected_version=-1)
        batch, _cursor = store.read_since(head)

        assert [stream for stream, _e in batch] == ["mcp_server:new"]


class TestUpgradingAnExistingDatabase:
    def test_rows_written_before_the_column_are_still_delivered(self, store) -> None:
        # An installation upgrades with history already in the table. Those rows
        # have a NULL `xact_id`; they must read as the oldest possible
        # transaction, not be silently excluded by the watermark comparison.
        store.append("mcp_server:legacy", [_event("legacy")], expected_version=-1)
        with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {store._events_table} SET xact_id = NULL")
            conn.commit()

        batch, _cursor = store.read_since(BEGINNING)

        assert [stream for stream, _e in batch] == ["mcp_server:legacy"]

    def test_adding_the_column_twice_is_harmless(self, store) -> None:
        # `initialize()` runs on every process start, and three replicas run it
        # at once during a rollout.
        store.initialize()
        store.initialize()

        store.append("mcp_server:a", [_event("a")], expected_version=-1)
        assert len(store.read_since(BEGINNING)[0]) == 1
