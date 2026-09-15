"""The in-memory metrics history store has one connection, and every use of it is serialized.

`MetricsHistoryStore()` with no config is `SQLiteConfig(path=":memory:")`, the
default when no persistence backend is selected. `SQLiteConnectionFactory`
keeps one connection for an in-memory database, because each new connection
to `:memory:` is a new, empty database, and `get_connection()` handed that one
connection to every thread with no lock. Two things went wrong:

* On Python 3.12 and later, two threads running the same SQL text on one
  connection can be handed the same prepared statement from the connection's
  statement cache. One thread then resets it under the other, and the reader
  gets `InterfaceError: bad parameter or other API misuse`, a row with columns
  missing, or `NULL` where a value was stored.
* A connection has one transaction. A read that ran while another thread was
  inside `record_snapshot` saw that batch half written, and the reader's own
  `commit()` committed it halfway.

A file-backed factory gives each thread its own connection and never had
either problem.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
import sys
import threading
import time

import pytest

from mcp_hangar.infrastructure.persistence.database_common import SQLiteConfig, SQLiteConnectionFactory
from mcp_hangar.infrastructure.persistence.metrics_history_store import MetricPoint, MetricsHistoryStore

SERVER = "server-a"
STALE_SERVER = "server-stale"
RETENTION_DAYS = 7
BATCHES = 150
FRESH_PER_BATCH = 6
STALE_PER_BATCH = 4
READERS = 4

#: Fresh points sit after this instant, stale ones a day past the retention
#: window before it, so `prune()` deletes exactly the stale ones.
BASE = time.time()
STALE_BASE = BASE - (RETENTION_DAYS + 1) * 86_400


def _fresh(seq: int) -> MetricPoint:
    """The `seq`th fresh point. Every field is derived from `seq`, so a read can check each one exactly."""
    return MetricPoint(
        mcp_server_id=SERVER,
        metric_name=f"metric-{seq % 5}",
        value=float(seq),
        recorded_at=BASE + seq,
    )


def _stale(seq: int) -> MetricPoint:
    return MetricPoint(
        mcp_server_id=STALE_SERVER,
        metric_name="metric-stale",
        value=float(seq),
        recorded_at=STALE_BASE + seq,
    )


def _batch(n: int) -> list[MetricPoint]:
    """What the writer records in its `n`th snapshot: fresh points to keep, stale ones for `prune` to delete."""
    fresh = [_fresh(n * FRESH_PER_BATCH + j) for j in range(FRESH_PER_BATCH)]
    stale = [_stale(n * STALE_PER_BATCH + j) for j in range(STALE_PER_BATCH)]
    return fresh + stale


def _check_fresh(points: Sequence[MetricPoint], at_least: int) -> None:
    """What any read of the fresh points must return, whatever it raced.

    Each snapshot is one commit, and snapshots are recorded in order, so a read
    sees whole batches: exactly the first `len(points)` fresh points, in order.
    A row read through a statement another thread reset breaks that: it comes
    back short, with `None`, or as the wrong point. A read inside the writer's
    open transaction breaks it too: it sees part of a batch.
    """
    assert len(points) >= at_least, (len(points), at_least)
    assert len(points) % FRESH_PER_BATCH == 0, f"a read saw part of a snapshot: {len(points)} points"
    assert list(points) == [_fresh(seq) for seq in range(len(points))]


@pytest.fixture
def fine_switching() -> Iterator[None]:
    """Switch threads as often as the interpreter allows, so reads and writes interleave."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def _run(targets: Sequence[Callable[[], None]]) -> None:
    threads = [threading.Thread(target=target) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads), "a thread hung"


class TestQueriesRacingTheWorkerOnOneInMemoryStore:
    def test_every_query_returns_exactly_the_points_recorded(self, fine_switching: None) -> None:
        """One thread records and prunes, as the snapshot worker does; others query at the same time."""
        store = MetricsHistoryStore(retention_days=RETENTION_DAYS)
        writer_done = threading.Event()
        pruned: list[int] = []
        failures: list[BaseException] = []

        def writer() -> None:
            try:
                for n in range(BATCHES):
                    store.record_snapshot(_batch(n))
                    pruned.append(store.prune())
            except Exception as e:  # noqa: BLE001 -- collected and asserted on below
                failures.append(e)
            finally:
                writer_done.set()

        def reader() -> None:
            seen = 0
            while True:
                finished = writer_done.is_set()
                try:
                    fresh = store.query(mcp_server_id=SERVER, from_ts=BASE, limit=10_000)
                    _check_fresh(fresh, at_least=seen)
                    seen = len(fresh)
                    stale = store.query(mcp_server_id=STALE_SERVER, limit=10_000)
                    # Between a snapshot and its prune there is one batch of
                    # stale points; after the prune, none. Never a part of one.
                    assert len(stale) in (0, STALE_PER_BATCH), len(stale)
                    assert all(p.mcp_server_id == STALE_SERVER and p.recorded_at < BASE for p in stale), stale
                except Exception as e:  # noqa: BLE001 -- collected and asserted on below
                    failures.append(e)
                if finished:
                    return

        try:
            _run([writer, *(reader for _ in range(READERS))])

            assert failures == [], f"{len(failures)} queries or writes failed, e.g. {failures[:3]!r}"
            assert pruned == [STALE_PER_BATCH] * BATCHES, "a prune deleted the wrong number of rows"
            final = store.query(limit=10_000)
            assert final == [_fresh(seq) for seq in range(BATCHES * FRESH_PER_BATCH)]
        finally:
            store._factory.close()


class TestTheInMemoryFactoryHoldsItsLock:
    """`get_connection()` on the shared in-memory connection is held for the caller's whole `with` block.

    Deterministic: each test parks one thread inside the block and checks what
    another thread can do meanwhile.
    """

    @staticmethod
    def _hold(factory: SQLiteConnectionFactory, inside: threading.Event, release: threading.Event) -> threading.Thread:
        def hold() -> None:
            with factory.get_connection():
                inside.set()
                release.wait(timeout=30)

        thread = threading.Thread(target=hold)
        thread.start()
        assert inside.wait(timeout=30), "the holder never got the connection"
        return thread

    def test_a_second_thread_waits_until_the_first_leaves_its_block(self) -> None:
        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        inside, release, entered = threading.Event(), threading.Event(), threading.Event()
        try:
            holder = self._hold(factory, inside, release)

            def enter() -> None:
                with factory.get_connection():
                    entered.set()

            other = threading.Thread(target=enter)
            other.start()
            assert not entered.wait(timeout=0.5), "a second thread got the shared connection while the first held it"
            release.set()
            assert entered.wait(timeout=30), "the second thread never got the connection"
            holder.join(timeout=30)
            other.join(timeout=30)
            assert not holder.is_alive() and not other.is_alive(), "a thread hung"
        finally:
            release.set()
            factory.close()

    def test_close_waits_for_a_caller_still_inside_its_block(self) -> None:
        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        inside, release, closed = threading.Event(), threading.Event(), threading.Event()
        try:
            holder = self._hold(factory, inside, release)

            def close() -> None:
                factory.close()
                closed.set()

            closer = threading.Thread(target=close)
            closer.start()
            assert not closed.wait(timeout=0.5), "close() ran while a caller was using the connection"
            release.set()
            assert closed.wait(timeout=30), "close() never ran"
            holder.join(timeout=30)
            closer.join(timeout=30)
            assert not holder.is_alive() and not closer.is_alive(), "a thread hung"
        finally:
            release.set()
            factory.close()

    def test_a_nested_block_on_one_thread_does_not_deadlock(self) -> None:
        factory = SQLiteConnectionFactory(SQLiteConfig(path=":memory:"))
        same: list[bool] = []

        def nested() -> None:
            with factory.get_connection() as outer, factory.get_connection() as inner:
                same.append(outer is inner)

        try:
            thread = threading.Thread(target=nested)
            thread.start()
            thread.join(timeout=30)
            assert not thread.is_alive(), "a nested get_connection() deadlocked"
            assert same == [True]
        finally:
            factory.close()

    def test_a_file_backed_factory_gives_each_thread_its_own_unlocked_connection(self, tmp_path: Path) -> None:
        factory = SQLiteConnectionFactory(SQLiteConfig(path=str(tmp_path / "history.db")))
        inside, release, entered = threading.Event(), threading.Event(), threading.Event()
        connections: list[object] = []
        try:
            holder = self._hold(factory, inside, release)

            def enter() -> None:
                with factory.get_connection() as conn:
                    connections.append(conn)
                    entered.set()
                factory.close()  # closes this thread's connection only

            other = threading.Thread(target=enter)
            other.start()
            assert entered.wait(timeout=30), "a file-backed connection waited on another thread"
            release.set()
            holder.join(timeout=30)
            other.join(timeout=30)
            assert not holder.is_alive() and not other.is_alive(), "a thread hung"
            with factory.get_connection() as mine:
                assert connections[0] is not mine
        finally:
            release.set()
            factory.close()
