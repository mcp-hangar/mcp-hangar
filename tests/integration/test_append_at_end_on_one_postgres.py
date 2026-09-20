"""Replicas appending at the end of one PostgreSQL stream lose nothing.

The fake in `tests/unit/test_postgres_event_store.py` interprets one statement
at a time, so it cannot show two writers queueing on a row lock. This runs
against a real server with several processes. Each one is a replica with its
own bus, its own store handle and several threads, and all of them append at
the end of one stream in one database.

It fails without the fix. The bus used to read the version and then append at
it, so another replica writing in between turned the append into a
`ConcurrencyError` and the batch was dropped.

Opt-in, like the other `live` tests: set `HANGAR_TEST_POSTGRES_DSN`. See
`tests/integration/test_postgres_tail_does_not_skip.py` for the one-liner that
starts a server.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

import mcp_hangar
from mcp_hangar.domain.contracts.event_store import ConcurrencyError
from mcp_hangar.domain.events import HealthCheckPassed
from mcp_hangar.infrastructure.persistence.backends.postgresql.event_store import PostgresEventStore
from mcp_hangar.stream_ids import MCP_SERVER, stream_id_for

pytestmark = pytest.mark.live

DSN = os.environ.get("HANGAR_TEST_POSTGRES_DSN", "")

psycopg2 = pytest.importorskip("psycopg2", reason="the postgres extra is not installed")

if not DSN:
    pytest.skip("HANGAR_TEST_POSTGRES_DSN is not set", allow_module_level=True)

REPLICAS = 3
THREADS_PER_REPLICA = 4
BATCHES_PER_THREAD = 40

# One replica: a bus over its own store handle, several threads publishing the
# way the tool-call handler and the background workers do.
_REPLICA = r"""
import sys
import threading
from contextlib import contextmanager

import psycopg2

from mcp_hangar.domain.events import HealthCheckPassed
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.backends.postgresql.event_store import PostgresEventStore
from mcp_hangar.stream_ids import MCP_SERVER

dsn, prefix, threads, batches = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])


class Factory:
    @contextmanager
    def get_connection(self):
        conn = psycopg2.connect(dsn)
        try:
            yield conn
        finally:
            conn.close()


bus = EventBus(event_store=PostgresEventStore(Factory(), table_prefix=prefix))
failures = []


def publish():
    for i in range(batches):
        batch = [HealthCheckPassed(mcp_server_id="svc", duration_ms=float(i)) for _ in range(1 + i % 2)]
        try:
            bus.publish_aggregate_events(MCP_SERVER, "svc", batch)
        except Exception as e:
            failures.append(repr(e))


workers = [threading.Thread(target=publish) for _ in range(threads)]
for worker in workers:
    worker.start()
for worker in workers:
    worker.join()
sys.stderr.write(f"RESULT failures={len(failures)} first={failures[:1]}\n")
sys.exit(1 if failures else 0)
"""


class _DirectFactory:
    @contextmanager
    def get_connection(self):
        conn = psycopg2.connect(DSN)
        try:
            yield conn
        finally:
            conn.close()


@pytest.fixture
def prefix() -> Iterator[str]:
    """Tables of this test's own, dropped afterwards."""
    name = f"append_end_{uuid4().hex[:8]}_"
    yield name
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {name}events, {name}streams, {name}snapshots")
        conn.commit()


def _store(prefix: str) -> PostgresEventStore:
    store = PostgresEventStore(_DirectFactory(), table_prefix=prefix)
    store.initialize()
    return store


def _versions(prefix: str, stream_id: str) -> list[int]:
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT stream_version FROM {prefix}events WHERE stream_id = %s ORDER BY stream_version",
            (stream_id,),
        )
        rows = [row[0] for row in cur.fetchall()]
        conn.commit()
    return rows


def _event() -> HealthCheckPassed:
    return HealthCheckPassed(mcp_server_id="svc", duration_ms=1.0)


def test_replicas_appending_at_the_end_lose_nothing(prefix: str) -> None:
    _store(prefix)  # the schema exists before the replicas start writing
    # The children import the same tree this test imported, fixed or not.
    env = {**os.environ, "PYTHONPATH": str(Path(mcp_hangar.__file__).parents[1])}
    replicas = [
        subprocess.Popen(
            [sys.executable, "-c", _REPLICA, DSN, prefix, str(THREADS_PER_REPLICA), str(BATCHES_PER_THREAD)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(REPLICAS)
    ]
    outcomes = [(replica.communicate(timeout=240)[1], replica.returncode) for replica in replicas]

    results = [line for stderr, _ in outcomes for line in stderr.splitlines() if line.startswith("RESULT")]
    assert [code for _, code in outcomes] == [0] * REPLICAS, results

    per_thread = sum(1 + i % 2 for i in range(BATCHES_PER_THREAD))
    expected = REPLICAS * THREADS_PER_REPLICA * per_thread
    stream_id = stream_id_for(MCP_SERVER, "svc")
    assert _versions(prefix, stream_id) == list(range(expected))
    assert _store(prefix).get_stream_version(stream_id) == expected - 1


def test_a_claimed_version_is_still_checked(prefix: str) -> None:
    store = _store(prefix)
    stream_id = stream_id_for(MCP_SERVER, "svc")
    assert store.append_at_end(stream_id, [_event()]) == 0

    with pytest.raises(ConcurrencyError) as exc_info:
        store.append(stream_id, [_event()], expected_version=-1)
    assert (exc_info.value.expected, exc_info.value.actual) == (-1, 0)

    assert store.append(stream_id, [_event()], expected_version=0) == 1
    assert _versions(prefix, stream_id) == [0, 1]


def test_writers_racing_on_one_claim_have_exactly_one_winner(prefix: str) -> None:
    store = _store(prefix)
    stream_id = stream_id_for(MCP_SERVER, "svc")
    start = threading.Barrier(8)
    won: list[int] = []
    lost: list[ConcurrencyError] = []

    def claim() -> None:
        start.wait()
        try:
            won.append(store.append(stream_id, [_event()], expected_version=-1))
        except ConcurrencyError as e:
            lost.append(e)

    writers = [threading.Thread(target=claim) for _ in range(8)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(timeout=60)

    assert won == [0]
    assert len(lost) == 7
    assert _versions(prefix, stream_id) == [0]
