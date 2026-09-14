"""The in-memory SQLite event store has one connection, and every use of it is serialized.

`SQLiteEventStore(":memory:")` keeps one connection for its whole life, because
each new connection to `:memory:` is a new, empty database. Appends held
`self._lock`; the reads on that same connection did not. Two things went wrong:

* On Python 3.12 and later, two threads running the same SQL text on one
  connection can be handed the same prepared statement from the connection's
  statement cache. One thread then resets it under the other, and the reader
  gets `InterfaceError: bad parameter or other API misuse`, a row with columns
  missing, or `NULL` where an event type was stored. It passed on 3.11 by luck.
* A connection has one transaction. A read that ran while another thread was
  inside `_append`'s `BEGIN IMMEDIATE` saw that thread's uncommitted rows, and
  kept them after that append rolled back. That holds on every version.

File-backed stores open a connection per call and never had either problem.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
import sys
import threading

import pytest

from mcp_hangar.domain.events import DomainEvent, McpServerStarted
from mcp_hangar.infrastructure.persistence import SQLiteEventStore
from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer

STREAM = "mcp_server:shared"
WRITERS = 4
EVENTS_PER_WRITER = 40
READERS = 4


def _event(writer: str, n: int) -> McpServerStarted:
    """The `n`th event one writer appends; `tools_count` carries `n` so the order can be checked."""
    return McpServerStarted(mcp_server_id=writer, mode="subprocess", tools_count=n, startup_duration_ms=0.0)


def _close(store: SQLiteEventStore) -> None:
    conn = getattr(store, "_persistent_conn", None)
    if conn is not None:
        conn.close()


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


def _check_stream(events: Sequence[DomainEvent], at_least: int) -> None:
    """What any read of the stream must return, whatever it raced.

    Each writer appends its events one at a time, in order, so in the stream
    every writer's `tools_count` runs 0, 1, 2, ... with no gap. A row read
    through a statement another thread reset breaks that: it comes back short,
    as `None`, or as the wrong event.
    """
    assert len(events) >= at_least, (len(events), at_least)
    last: dict[str, int] = {}
    for event in events:
        assert isinstance(event, McpServerStarted), event
        expected = last.get(event.mcp_server_id, -1) + 1
        assert event.tools_count == expected, (event.mcp_server_id, event.tools_count, expected)
        last[event.mcp_server_id] = event.tools_count


class TestReadsRacingAppendsOnOneInMemoryStore:
    def test_every_read_returns_the_events_actually_appended(self, fine_switching: None) -> None:
        store = SQLiteEventStore(":memory:")
        writers_done = threading.Event()
        returned_versions: list[int] = []
        failures: list[Exception] = []

        def writer(name: str) -> None:
            try:
                for n in range(EVENTS_PER_WRITER):
                    returned_versions.append(store.append_at_end(STREAM, [_event(name, n)]))
            except Exception as e:  # noqa: BLE001 -- collected and asserted on below
                failures.append(e)

        def reader() -> None:
            last_version = -1
            while True:
                finished = writers_done.is_set()
                try:
                    version = store.get_stream_version(STREAM)
                    assert version >= last_version, (version, last_version)
                    last_version = version
                    _check_stream(store.read_stream(STREAM), at_least=version + 1)
                    rows = list(store.read_all(limit=10_000))
                    assert [position for position, _, _ in rows] == sorted({position for position, _, _ in rows})
                    assert {stream_id for _, stream_id, _ in rows} <= {STREAM}
                    _check_stream([event for _, _, event in rows], at_least=version + 1)
                except Exception as e:  # noqa: BLE001 -- collected and asserted on below
                    failures.append(e)
                if finished:
                    return

        writer_threads = [lambda name=f"writer-{i}": writer(name) for i in range(WRITERS)]
        reader_threads = [reader for _ in range(READERS)]

        def writers_then_signal() -> None:
            _run(writer_threads)
            writers_done.set()

        try:
            _run([writers_then_signal, *reader_threads])

            assert failures == [], f"{len(failures)} reads or writes failed, e.g. {failures[:3]!r}"
            total = WRITERS * EVENTS_PER_WRITER
            assert sorted(returned_versions) == list(range(total)), "two appends were told the same version"
            final = store.read_stream(STREAM)
            assert len(final) == total
            _check_stream(final, at_least=total)
            assert store.get_stream_version(STREAM) == total - 1
            assert store.get_event_count() == total
            assert store.list_streams() == [STREAM]
        finally:
            _close(store)


class _FailsMidAppend(EventSerializer):
    """Serializes normally until it meets one event: then it signals, waits to be released, and fails."""

    def __init__(self, poison: str) -> None:
        super().__init__()
        self._poison = poison
        self.inside = threading.Event()
        self.release = threading.Event()

    def serialize(self, event: DomainEvent) -> tuple[str, str]:
        if isinstance(event, McpServerStarted) and event.mcp_server_id == self._poison:
            self.inside.set()
            self.release.wait(timeout=30)
            raise RuntimeError("serialization failed halfway through the append")
        return super().serialize(event)


class TestAReadDoesNotSeeAnotherThreadsOpenAppend:
    def test_a_read_during_an_append_that_rolls_back_never_sees_its_rows(self) -> None:
        """The append has written one row inside its transaction and is stopped before the next.

        A read from another thread must not return that row. On the shared
        connection an unserialized read ran inside the append's transaction and
        returned it, and the append then rolled back. Deterministic: nothing
        here depends on how the threads are scheduled.
        """
        serializer = _FailsMidAppend(poison="poison")
        store = SQLiteEventStore(":memory:", serializer=serializer)
        try:
            store.append(STREAM, [_event("writer", 0)], expected_version=-1)
            append_error: list[Exception] = []
            seen_events: list[DomainEvent] = []
            seen: dict[str, int] = {}

            def append_that_fails() -> None:
                try:
                    store.append(STREAM, [_event("writer", 1), _event("poison", 0)], expected_version=0)
                except RuntimeError as e:
                    append_error.append(e)

            def read() -> None:
                seen_events.extend(store.read_stream(STREAM))
                seen["version"] = store.get_stream_version(STREAM)
                seen["count"] = store.get_event_count()

            appender = threading.Thread(target=append_that_fails)
            appender.start()
            assert serializer.inside.wait(timeout=30), "the append never reached the failing event"
            reader = threading.Thread(target=read)
            reader.start()
            reader.join(timeout=0.5)  # give an unserialized read time to finish inside the transaction
            serializer.release.set()
            appender.join(timeout=30)
            reader.join(timeout=30)

            assert not appender.is_alive() and not reader.is_alive(), "a thread hung"
            assert len(append_error) == 1, "the append was meant to fail and roll back"
            _check_stream(seen_events, at_least=1)
            assert len(seen_events) == 1, "the read saw a row that was rolled back"
            assert seen == {"version": 0, "count": 1}
            after = store.read_stream(STREAM)
            _check_stream(after, at_least=1)
            assert len(after) == 1
        finally:
            serializer.release.set()
            _close(store)
