"""A batch appended at the end of a stream is never dropped.

`EventBus.publish_to_stream` used to resolve `APPEND_AT_END` in two separate
calls: read the stream's version, then append at it. Another writer could get
in between: a concurrent tool call on the same server, the health or gc worker,
or another replica. The append then failed with `ConcurrencyError`, and the bus
re-raised it without delivering. The tool-call handler let it escape. The
events had already been drained from the aggregate, so the store had no record,
and the audit, metrics, security and enforcement handlers got nothing. A call
that ran upstream was reported as failed.

What is pinned here:

- many threads appending at the end of one stream lose nothing, on every store
  that runs in CI, and every batch is delivered;
- processes sharing one SQLite file lose nothing either, which a per-process
  lock alone would not give;
- a caller that does claim a version still gets its `ConcurrencyError`;
- a store that never overrode `append_at_end` retries a bounded number of
  times, and a batch it cannot place is delivered like an outage, not dropped;
- concurrent tool calls to one server, with background writers on the same
  stream, are all reported successful and all recorded;
- a publish that fails after the call ran never changes the call's outcome.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

import mcp_hangar
from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.application.commands import InvokeToolCommand
from mcp_hangar.application.commands.handlers import InvokeToolHandler
from mcp_hangar.application.event_handlers.audit_handler import AuditEventHandler, InMemoryAuditStore
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.contracts.event_store import ConcurrencyError, IEventStore
from mcp_hangar.domain.events import DomainEvent, HealthCheckPassed, ToolInvocationCompleted, ToolInvocationRequested
from mcp_hangar.domain.exceptions import ToolInvocationError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.observability.metrics_event_handler import MetricsEventHandler
from mcp_hangar.infrastructure.persistence import InMemoryEventStore
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore
from mcp_hangar.stream_ids import MCP_SERVER, stream_id_for

THREADS = 8
BATCHES_PER_THREAD = 100

PROCESSES = 4
BATCHES_PER_PROCESS = 150

CALLERS = 8
CALLS_PER_CALLER = 40
BACKGROUND_WRITERS = 2
BACKGROUND_BATCHES_CAP = 1_000

_REPLY = {"result": {"content": [{"type": "text", "text": "42"}]}}
_RESULT = _REPLY["result"]

Store = InMemoryEventStore | SQLiteEventStore


def _in_memory(tmp_path: Path) -> Store:
    return InMemoryEventStore()


def _sqlite_in_memory(tmp_path: Path) -> Store:
    return SQLiteEventStore(":memory:")


def _sqlite_file(tmp_path: Path) -> Store:
    return SQLiteEventStore(tmp_path / "events.db")


EVERY_STORE = pytest.mark.parametrize(
    "make_store",
    [_in_memory, _sqlite_in_memory, _sqlite_file],
    ids=["in-memory", "sqlite-in-memory", "sqlite-file"],
)


@pytest.fixture
def fine_switching() -> Iterator[None]:
    """Switch threads as often as the interpreter allows, so a two-step race interleaves.

    Two threads and this were all the original reproduction needed.
    """
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def _event(server_id: str = "svc") -> HealthCheckPassed:
    return HealthCheckPassed(mcp_server_id=server_id, duration_ms=1.0)


def _versions(store: Store, stream_id: str) -> list[int]:
    """Every stored version of a stream, in order, read from the store's own rows."""
    if isinstance(store, InMemoryEventStore):
        return [stored.stream_version for stored in store._streams[stream_id].events]
    conn = store._connect()
    try:
        rows = conn.execute(
            "SELECT stream_version FROM events WHERE stream_id = ? ORDER BY stream_version",
            (stream_id,),
        ).fetchall()
    finally:
        if not store._is_memory:
            conn.close()
    return [row[0] for row in rows]


def _run(target: Callable[[], None], count: int) -> None:
    threads = [threading.Thread(target=target) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads), "a writer hung"


class TestManyThreadsAppendingToOneStream:
    @EVERY_STORE
    def test_nothing_is_lost_and_every_batch_is_delivered(
        self, make_store: Callable[[Path], Store], tmp_path: Path, fine_switching: None
    ) -> None:
        store = make_store(tmp_path)
        bus = EventBus(event_store=store)
        delivered: list[DomainEvent] = []
        bus.subscribe_to_all(delivered.append, kind=HandlerKind.EFFECT)
        sent: list[DomainEvent] = []
        raised: list[Exception] = []

        def publish() -> None:
            for i in range(BATCHES_PER_THREAD):
                batch: list[DomainEvent] = [_event() for _ in range(1 + i % 3)]
                sent.extend(batch)
                try:
                    bus.publish_aggregate_events(MCP_SERVER, "svc", batch)
                except Exception as e:  # noqa: BLE001 -- collected and asserted on below
                    raised.append(e)

        _run(publish, THREADS)

        stream_id = stream_id_for(MCP_SERVER, "svc")
        assert raised == [], f"{len(raised)} of {THREADS * BATCHES_PER_THREAD} batches raised, e.g. {raised[:1]!r}"
        assert _versions(store, stream_id) == list(range(len(sent)))
        assert sorted(e.event_id for e in store.read_stream(stream_id)) == sorted(e.event_id for e in sent)
        assert sorted(e.event_id for e in delivered) == sorted(e.event_id for e in sent)


# One writer process: its own store handle on the shared file, publishing the
# way the bus does for every aggregate batch. It waits at a start line so that
# all of them write at once rather than one after another as they finish
# importing.
_WRITER = r"""
import os
import sys
import time

from mcp_hangar.domain.events import HealthCheckPassed
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore
from mcp_hangar.stream_ids import MCP_SERVER

db, batches, ready, go = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
bus = EventBus(event_store=SQLiteEventStore(db))
open(f"{ready}{os.getpid()}", "w").close()
deadline = time.monotonic() + 60
while not os.path.exists(go) and time.monotonic() < deadline:
    time.sleep(0.005)

failures = []
for i in range(batches):
    try:
        bus.publish_aggregate_events(MCP_SERVER, "svc", [HealthCheckPassed(mcp_server_id="svc", duration_ms=float(i))])
    except Exception as e:
        failures.append(repr(e))
sys.stderr.write(f"RESULT failures={len(failures)} first={failures[:1]}\n")
sys.exit(1 if failures else 0)
"""


class TestProcessesSharingOneSqliteFile:
    def test_nothing_is_lost(self, tmp_path: Path) -> None:
        """A per-process lock is not enough: several processes can open the file."""
        db = tmp_path / "events.db"
        SQLiteEventStore(db)  # the schema exists before the writers start
        ready, go = tmp_path / "ready-", tmp_path / "go"
        # The children import the same tree this test imported, fixed or not.
        env = {**os.environ, "PYTHONPATH": str(Path(mcp_hangar.__file__).parents[1])}
        writers = [
            subprocess.Popen(
                [sys.executable, "-c", _WRITER, str(db), str(BATCHES_PER_PROCESS), str(ready), str(go)],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(PROCESSES)
        ]
        deadline = time.monotonic() + 60
        while len(list(tmp_path.glob("ready-*"))) < PROCESSES and time.monotonic() < deadline:
            time.sleep(0.01)
        go.touch()
        outcomes = [(writer.communicate(timeout=240)[1], writer.returncode) for writer in writers]

        results = [line for stderr, _ in outcomes for line in stderr.splitlines() if line.startswith("RESULT")]
        assert [code for _, code in outcomes] == [0] * PROCESSES, results
        stream_id = stream_id_for(MCP_SERVER, "svc")
        assert _versions(SQLiteEventStore(db), stream_id) == list(range(PROCESSES * BATCHES_PER_PROCESS))


class TestAClaimedVersionIsStillChecked:
    @EVERY_STORE
    def test_a_stale_claim_raises_and_is_not_delivered(
        self, make_store: Callable[[Path], Store], tmp_path: Path
    ) -> None:
        store = make_store(tmp_path)
        bus = EventBus(event_store=store)
        delivered: list[DomainEvent] = []
        bus.subscribe_to_all(delivered.append, kind=HandlerKind.EFFECT)
        stream_id = stream_id_for(MCP_SERVER, "svc")
        bus.publish_aggregate_events(MCP_SERVER, "svc", [_event()])

        with pytest.raises(ConcurrencyError) as exc_info:
            bus.publish_to_stream(stream_id, [_event()], expected_version=-1)

        assert (exc_info.value.expected, exc_info.value.actual) == (-1, 0)
        assert len(delivered) == 1, "a conflicting batch is the caller's to handle, not delivered"
        assert bus.publish_to_stream(stream_id, [_event()], expected_version=0) == 1

    @EVERY_STORE
    def test_writers_racing_on_one_claim_have_exactly_one_winner(
        self, make_store: Callable[[Path], Store], tmp_path: Path
    ) -> None:
        store = make_store(tmp_path)
        stream_id = stream_id_for(MCP_SERVER, "svc")
        start = threading.Barrier(THREADS)
        won: list[int] = []
        lost: list[ConcurrencyError] = []

        def claim() -> None:
            start.wait()
            try:
                won.append(store.append(stream_id, [_event()], expected_version=-1))
            except ConcurrencyError as e:
                lost.append(e)

        _run(claim, THREADS)

        assert won == [0]
        assert len(lost) == THREADS - 1
        assert _versions(store, stream_id) == [0]


class _Contended(InMemoryEventStore):
    """A store that never overrode `append_at_end`, and another writer that gets in first."""

    def __init__(self, losses: int) -> None:
        super().__init__()
        self.losses = losses
        self.attempts = 0

    def append_at_end(self, stream_id: str, events: list[DomainEvent]) -> int:
        # The contract's default, not the in-memory store's atomic override.
        return IEventStore.append_at_end(self, stream_id, events)

    def append(self, stream_id: str, events: list[DomainEvent], expected_version: int) -> int:
        self.attempts += 1
        if self.losses:
            self.losses -= 1
            # The other writer moves the stream after the version was read.
            super().append(stream_id, [_event()], super().get_stream_version(stream_id))
        return super().append(stream_id, events, expected_version)


class TestAStoreWithoutItsOwnAppendAtEnd:
    def test_it_reads_again_after_losing_a_race(self) -> None:
        store = _Contended(losses=3)

        assert store.append_at_end("s", [_event()]) == 3
        assert store.attempts == 4
        assert _versions(store, "s") == [0, 1, 2, 3]

    def test_it_gives_up_after_a_bounded_number_of_attempts(self) -> None:
        store = _Contended(losses=10**6)

        with pytest.raises(ConcurrencyError):
            store.append_at_end("s", [_event()])
        assert store.attempts == IEventStore._APPEND_AT_END_ATTEMPTS

    def test_a_batch_it_could_not_place_is_delivered_and_reported(self) -> None:
        bus = EventBus(event_store=_Contended(losses=10**6))
        delivered: list[DomainEvent] = []
        bus.subscribe_to_all(delivered.append, kind=HandlerKind.EFFECT)
        batch: list[DomainEvent] = [_event(), _event()]

        with capture_logs() as logs:
            bus.publish_aggregate_events(MCP_SERVER, "svc", batch)

        assert delivered == batch
        failures = [entry for entry in logs if entry.get("event") == "event_persistence_failed"]
        assert len(failures) == 1
        assert failures[0]["log_level"] == "error"


def _server(reply: Any = None) -> McpServer:
    """A real aggregate whose upstream answers at once, built as the call-counting tests build one."""
    server = McpServer(mcp_server_id=uuid4().hex, mode="subprocess", command=["echo"])
    server.ensure_ready = Mock()  # type: ignore[method-assign]
    # A started server: invoke_tool refuses one that is not READY, whatever
    # ensure_ready() did.
    server._state = McpServerState.READY
    upstream = Mock(side_effect=reply) if isinstance(reply, Exception) else _Upstream()
    server._client = MagicMock(call=upstream)
    server._tools.update_from_list([{"name": "add"}])
    return server


class _Upstream:
    """Answers every call at once, and counts the calls under a lock.

    `Mock.call_count` is a plain `+= 1`, so it loses increments when calls
    arrive from several threads at once.
    """

    def __init__(self) -> None:
        self.call_count = 0
        self._lock = threading.Lock()

    def __call__(self, *args: object, **kwargs: object) -> dict[str, Any]:
        with self._lock:
            self.call_count += 1
        return _REPLY


def _successes(server_id: str) -> float:
    return sum(
        sample.value
        for sample in prometheus_metrics.TOOL_CALLS_TOTAL.collect()
        if sample.labels.get("mcp_server") == server_id and sample.labels.get("status") == "success"
    )


class TestConcurrentToolCallsToOneServer:
    @pytest.mark.parametrize("make_store", [_in_memory, _sqlite_file], ids=["in-memory", "sqlite-file"])
    def test_every_call_that_ran_is_reported_and_recorded(
        self, make_store: Callable[[Path], Store], tmp_path: Path, fine_switching: None
    ) -> None:
        store = make_store(tmp_path)
        server = _server()
        events = EventBus(event_store=store)
        audit = InMemoryAuditStore()
        audited_types = ["ToolInvocationRequested", "ToolInvocationCompleted"]
        events.subscribe_to_all(
            AuditEventHandler(store=audit, include_event_types=audited_types).handle, kind=HandlerKind.EFFECT
        )
        events.subscribe_to_all(MetricsEventHandler().handle, kind=HandlerKind.EFFECT)
        commands = CommandBus()
        commands.register(InvokeToolCommand, InvokeToolHandler(Mock(get=Mock(return_value=server)), events))

        outcomes: list[object] = []
        background_failures: list[Exception] = []
        done = threading.Event()

        def call() -> None:
            for _ in range(CALLS_PER_CALLER):
                command = InvokeToolCommand(mcp_server_id=server.mcp_server_id, tool_name="add", arguments={})
                try:
                    outcomes.append(commands.send(command))
                except Exception as e:  # noqa: BLE001 -- collected and asserted on below
                    outcomes.append(e)

        def background() -> None:
            # What the health-check and gc workers do on every pass: publish
            # the server's events at the end of its stream.
            for _ in range(BACKGROUND_BATCHES_CAP):
                if done.is_set():
                    return
                try:
                    events.publish_aggregate_events(MCP_SERVER, server.mcp_server_id, [_event(server.mcp_server_id)])
                except Exception as e:  # noqa: BLE001 -- collected and asserted on below
                    background_failures.append(e)

        workers = [threading.Thread(target=background) for _ in range(BACKGROUND_WRITERS)]
        for worker in workers:
            worker.start()
        try:
            _run(call, CALLERS)
        finally:
            done.set()
            for worker in workers:
                worker.join(timeout=60)

        calls = CALLERS * CALLS_PER_CALLER
        failed = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert failed == [], (
            f"{len(failed)} of {calls} calls ran upstream and were reported failed, e.g. {failed[:1]!r}"
        )
        assert outcomes == [_RESULT] * calls
        assert server._client.call.call_count == calls
        assert background_failures == []

        stream_id = stream_id_for(MCP_SERVER, server.mcp_server_id)
        stored = store.read_stream(stream_id)
        assert _versions(store, stream_id) == list(range(len(stored)))
        for kind in (ToolInvocationRequested, ToolInvocationCompleted):
            assert len({e.correlation_id for e in stored if isinstance(e, kind)}) == calls, kind.__name__
        completed = audit.query(
            mcp_server_id=server.mcp_server_id, event_type="ToolInvocationCompleted", limit=10 * calls
        )
        assert len(completed) == calls
        assert _successes(server.mcp_server_id) == calls


class _RaisingBus:
    """A bus whose publish raises, as the old one did on a conflict."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def publish_aggregate_events(self, *args: object, **kwargs: object) -> int:
        raise self.error


PUBLISH_FAILURES = [ConcurrencyError("mcp_server:s", 35, 36), KeyError("stream"), OSError("disk full")]


def _publish_errors_counted(error: Exception) -> float:
    wanted = {"component": "event_publish", "error_type": type(error).__name__}
    return sum(sample.value for sample in prometheus_metrics.ERRORS_TOTAL.collect() if sample.labels == wanted)


class TestAPublishFailureAfterTheCallRan:
    @pytest.mark.parametrize("error", PUBLISH_FAILURES, ids=lambda e: type(e).__name__)
    def test_the_call_is_reported_as_it_ran(self, error: Exception) -> None:
        server = _server()
        handler = InvokeToolHandler(Mock(get=Mock(return_value=server)), _RaisingBus(error))
        counted_before = _publish_errors_counted(error)

        with capture_logs() as logs:
            result = handler.handle(
                InvokeToolCommand(mcp_server_id=server.mcp_server_id, tool_name="add", arguments={})
            )

        assert result == _RESULT
        failures = [entry for entry in logs if entry.get("event") == "event_publish_failed"]
        assert len(failures) == 1
        assert failures[0]["log_level"] == "error"
        assert failures[0]["event_types"] == ["ToolInvocationRequested", "ToolInvocationCompleted"]
        assert _publish_errors_counted(error) == counted_before + 1

    @pytest.mark.parametrize("error", PUBLISH_FAILURES, ids=lambda e: type(e).__name__)
    def test_a_call_that_failed_keeps_its_own_error(self, error: Exception) -> None:
        server = _server(reply=OSError("gone"))
        handler = InvokeToolHandler(Mock(get=Mock(return_value=server)), _RaisingBus(error))

        with pytest.raises(ToolInvocationError):
            handler.handle(InvokeToolCommand(mcp_server_id=server.mcp_server_id, tool_name="add", arguments={}))
