"""Health checks, saga runs and scheduled commands are bounded, explained spans (#1296).

Before this, background recovery was invisible to a trace: a health check that
degraded a server, the recovery saga it triggered, the restart that saga armed
on a timer and every orchestrated saga step reached the log and nothing else.

The tests drive the real worker loop on its own thread, a real `EventBus`, a
real `SagaManager` with the real `McpServerRecoverySaga`, a real `CommandBus`
and a real `threading.Timer`, because trace context is lost only across a real
thread: a test that called `_fire` inline would inherit the test's context and
prove nothing about the link.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from mcp_hangar.application.commands.commands import Command, GiveUpOnMcpServerCommand, StartMcpServerCommand
from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryOrchestrator
from mcp_hangar.application.ports.saga import Saga, SagaContext
from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga
from mcp_hangar.domain.events import McpServerDegraded
from mcp_hangar.domain.exceptions import RateLimitExceeded
from mcp_hangar.domain.value_objects.mcp_server import McpServerState
from mcp_hangar.gc import BackgroundWorker
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.saga_manager import SagaManager
from mcp_hangar.observability.conventions import Discovery, Health, HealthCheck, McpServer
from mcp_hangar.observability.conventions import Saga as SagaAttr

pytestmark = pytest.mark.otel_sdk

ERROR_TYPE = "error.type"
_TRACED_MODULES = (
    "mcp_hangar.gc",
    "mcp_hangar.infrastructure.saga_manager",
    "mcp_hangar.infrastructure.command_bus",
    "mcp_hangar.infrastructure.event_bus",
    "mcp_hangar.application.discovery.discovery_orchestrator",
)


@pytest.fixture
def exporter() -> Iterator[Any]:
    """A local provider, never registered globally, behind Hangar's own tracer wrapper."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from mcp_hangar.observability.tracing import _TextFreeTracer

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = _TextFreeTracer(provider.get_tracer("test-1296"))
    patches = [patch(f"{module}.get_tracer", return_value=tracer) for module in _TRACED_MODULES]
    for p in patches:
        p.start()
    try:
        yield memory
    finally:
        for p in patches:
            p.stop()


def _named(exporter: Any, name: str) -> list[Any]:
    return [s for s in exporter.get_finished_spans() if s.name == name]


def _wait_for(condition: Callable[[], bool], timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting on a background thread"
        time.sleep(0.005)


def _ancestors(exporter: Any, span: Any) -> list[str]:
    """Names of every span above ``span`` in its trace, nearest first."""
    by_id = {s.context.span_id: s for s in exporter.get_finished_spans()}
    names = []
    while span.parent is not None and span.parent.span_id in by_id:
        span = by_id[span.parent.span_id]
        names.append(span.name)
    return names


def _status(span: Any) -> str:
    return span.status.status_code.name


class _Recorder:
    """A command handler that remembers what it was sent, and can fail."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[Any] = []
        self.done = threading.Event()

    def handle(self, command: Any) -> str:
        self.sent.append(command)
        self.done.set()
        if self.error is not None:
            raise self.error
        return "ok"


class _Server:
    """A runtime shaped as `McpServerRuntime`: ready, unhealthy, degrades once."""

    def __init__(self, server_id: str, state: McpServerState, *, raises: bool = False) -> None:
        self.mcp_server_id = server_id
        self.state = state
        self.raises = raises
        self.checks = 0
        # Due once, then not for an hour: every later tick is idle.
        self.health = SimpleNamespace(consecutive_failures=3, get_health_check_interval=lambda *_a, **_k: 3600.0)
        self._events = [
            McpServerDegraded(mcp_server_id=server_id, consecutive_failures=3, total_failures=3, reason="test")
        ]

    def health_check(self) -> bool:
        self.checks += 1
        if self.raises:
            raise RuntimeError("probe broke")
        return False

    def collect_events(self) -> list[Any]:
        events, self._events = self._events, []
        return events

    def maybe_shutdown_idle(self) -> bool:
        return False


class _Ticks(dict):
    """The worker's mapping, counting the loop's ticks so a test can wait for idle ones."""

    ticks = 0

    def items(self):  # type: ignore[override]
        self.ticks += 1
        return super().items()


def _run_worker(servers: _Ticks, ticks: int) -> None:
    worker = BackgroundWorker(servers, interval_s=0.005, task="health_check", event_bus=servers.bus)  # type: ignore[arg-type]
    worker.start()
    try:
        _wait_for(lambda: servers.ticks >= ticks)
    finally:
        worker.stop()
        assert worker.join(5.0)


def _recovery_wiring(**saga_kwargs: Any) -> tuple[EventBus, CommandBus, SagaManager]:
    bus = EventBus()
    commands = CommandBus()
    manager = SagaManager(command_bus=commands, event_bus=bus)
    manager.register_event_saga(McpServerRecoverySaga(saga_manager=manager, **saga_kwargs))
    return bus, commands, manager


class TestHealthCheckSpans:
    def test_one_span_per_server_actually_checked_and_none_for_skips_or_idle_ticks(self, exporter: Any) -> None:
        bus, commands, _manager = _recovery_wiring(max_retries=0)
        commands.register(GiveUpOnMcpServerCommand, _Recorder())
        servers = _Ticks(
            ready=_Server("ready", McpServerState.READY),
            cold=_Server("cold", McpServerState.COLD),
            dead=_Server("dead", McpServerState.DEAD),
        )
        servers.bus = bus  # type: ignore[attr-defined]

        _run_worker(servers, ticks=6)

        [span] = _named(exporter, "mcp_server.health_check")
        assert span.attributes[McpServer.ID] == "ready"
        assert span.attributes[HealthCheck.OUTCOME] == HealthCheck.UNHEALTHY
        assert span.attributes[Health.CONSECUTIVE_FAILURES] == 3
        assert _status(span) == "UNSET", "an unhealthy answer is the check working, not failing"
        assert span.parent is None, "each check is its own trace, not one worker-lifetime span"

    def test_a_synchronous_recovery_command_is_a_child_of_the_check(self, exporter: Any) -> None:
        # max_retries=0: the first degradation gives up at once, on the publishing thread.
        bus, commands, _manager = _recovery_wiring(max_retries=0)
        give_up = _Recorder()
        commands.register(GiveUpOnMcpServerCommand, give_up)
        servers = _Ticks(ready=_Server("ready", McpServerState.READY))
        servers.bus = bus  # type: ignore[attr-defined]

        _run_worker(servers, ticks=3)

        assert len(give_up.sent) == 1
        [dispatch] = _named(exporter, "dispatch.GiveUpOnMcpServerCommand")
        [check] = _named(exporter, "mcp_server.health_check")
        assert dispatch.context.trace_id == check.context.trace_id
        assert "mcp_server.health_check" in _ancestors(exporter, dispatch)

    def test_a_check_that_raises_ends_error_with_outcome_error(self, exporter: Any) -> None:
        servers = _Ticks(ready=_Server("ready", McpServerState.READY, raises=True))
        servers.bus = EventBus()  # type: ignore[attr-defined]

        _run_worker(servers, ticks=3)

        spans = _named(exporter, "mcp_server.health_check")
        assert spans, "the check ran"
        for span in spans:
            assert span.attributes[HealthCheck.OUTCOME] == HealthCheck.ERROR
            assert _status(span) == "ERROR"
            assert span.attributes[ERROR_TYPE] == "RuntimeError"
            assert Health.CONSECUTIVE_FAILURES not in span.attributes


class TestScheduledCommands:
    def test_a_timer_fired_restart_starts_a_new_trace_linked_to_its_cause(self, exporter: Any) -> None:
        # max_retries=3 with a tiny backoff: the degradation arms a restart on a real timer.
        bus, commands, manager = _recovery_wiring(max_retries=3, initial_backoff_s=0.01)
        start = _Recorder()
        commands.register(StartMcpServerCommand, start)
        servers = _Ticks(ready=_Server("ready", McpServerState.READY))
        servers.bus = bus  # type: ignore[attr-defined]

        try:
            _run_worker(servers, ticks=3)
            _wait_for(lambda: len(_named(exporter, "saga.scheduled_command")) == 1)
        finally:
            manager.cancel_all_scheduled_commands()

        [check] = _named(exporter, "mcp_server.health_check")
        [fired] = _named(exporter, "saga.scheduled_command")
        assert fired.parent is None, "a timer is not a parent"
        assert fired.context.trace_id != check.context.trace_id
        [link] = fired.links
        assert link.context.trace_id == check.context.trace_id, "linked to the check that caused it"
        by_id = {s.context.span_id: s for s in exporter.get_finished_spans()}
        scheduler = by_id[link.context.span_id]
        assert scheduler.name == "mcp_server.health_check" or "mcp_server.health_check" in _ancestors(
            exporter, scheduler
        )
        assert fired.attributes[SagaAttr.COMMAND] == "StartMcpServerCommand"
        [dispatch] = _named(exporter, "dispatch.StartMcpServerCommand")
        assert dispatch.parent.span_id == fired.context.span_id
        assert _status(fired) == "UNSET"

    def test_an_unknown_cause_gives_no_link_and_no_parent(self, exporter: Any) -> None:
        commands = CommandBus()
        start = _Recorder()
        commands.register(StartMcpServerCommand, start)
        manager = SagaManager(command_bus=commands, event_bus=EventBus())

        manager.schedule_command(StartMcpServerCommand(mcp_server_id="x"), delay_s=0.01)
        _wait_for(lambda: len(_named(exporter, "saga.scheduled_command")) == 1)

        [fired] = _named(exporter, "saga.scheduled_command")
        assert fired.parent is None
        assert list(fired.links) == []

    def test_a_malformed_origin_gives_no_link(self, exporter: Any) -> None:
        from mcp_hangar.observability.tracing import new_trace_linked_to

        kwargs = new_trace_linked_to("00-not-a-trace-01")
        assert "links" not in kwargs
        assert "context" in kwargs, "still a new root, not the firing thread's context"

    def test_a_failed_scheduled_command_ends_error_and_a_refused_one_does_not(self, exporter: Any) -> None:
        commands = CommandBus()
        commands.register(StartMcpServerCommand, _Recorder(error=RuntimeError("broke")))
        commands.register(GiveUpOnMcpServerCommand, _Recorder(error=RateLimitExceeded(limit=1, window_seconds=1)))
        manager = SagaManager(command_bus=commands, event_bus=EventBus())

        manager.schedule_command(StartMcpServerCommand(mcp_server_id="x"), delay_s=0.01)
        manager.schedule_command(GiveUpOnMcpServerCommand(mcp_server_id="x", reason="r"), delay_s=0.01)
        _wait_for(lambda: len(_named(exporter, "saga.scheduled_command")) == 2)

        by_command = {s.attributes[SagaAttr.COMMAND]: s for s in _named(exporter, "saga.scheduled_command")}
        failed = by_command["StartMcpServerCommand"]
        assert _status(failed) == "ERROR"
        assert failed.attributes[ERROR_TYPE] == "RuntimeError"
        refused = by_command["GiveUpOnMcpServerCommand"]
        assert _status(refused) == "UNSET", "a refusal is not an operational failure (ADR-029 s5)"
        assert refused.attributes[ERROR_TYPE] == "RateLimitExceeded"


@dataclass(frozen=True)
class _Do(Command):
    what: str


@dataclass(frozen=True)
class _Undo(Command):
    what: str


class _ThreeSteps(Saga):
    @property
    def saga_type(self) -> str:
        return "three_steps"

    def configure(self, context: SagaContext) -> None:
        self.add_step("reserve", command=_Do("reserve"), compensation_command=_Undo("reserve"))
        self.add_step("wait", command=None)
        self.add_step("commit", command=_Do("commit"))


class _Steps:
    """Handles `_Do` and `_Undo`, failing the ones named."""

    def __init__(self, fail: set[str]) -> None:
        self.fail = fail

    def handle(self, command: Any) -> str:
        key = f"{type(command).__name__}:{command.what}"
        if key in self.fail:
            raise RuntimeError(key)
        return key


def _run_saga(fail: set[str]) -> None:
    commands = CommandBus()
    steps = _Steps(fail)
    commands.register(_Do, steps)
    commands.register(_Undo, steps)
    SagaManager(command_bus=commands, event_bus=EventBus()).start_saga(_ThreeSteps())


def _steps(span: Any) -> list[tuple[str, str]]:
    return [
        (e.attributes[SagaAttr.STEP_NAME], e.attributes[SagaAttr.STEP_OUTCOME])
        for e in span.events
        if e.name == SagaAttr.STEP_EVENT
    ]


class TestSagaRunSpans:
    def test_a_completed_run_records_every_step(self, exporter: Any) -> None:
        _run_saga(fail=set())

        [run] = _named(exporter, "saga.run")
        assert run.attributes[SagaAttr.TYPE] == "three_steps"
        assert run.attributes[SagaAttr.OUTCOME] == SagaAttr.COMPLETED
        assert _steps(run) == [("reserve", "completed"), ("wait", "no_action"), ("commit", "completed")]
        assert _status(run) == "UNSET"
        [dispatch, _] = _named(exporter, "dispatch._Do")
        assert dispatch.parent.span_id == run.context.span_id

    def test_a_failed_step_is_compensated_and_ends_error(self, exporter: Any) -> None:
        _run_saga(fail={"_Do:commit"})

        [run] = _named(exporter, "saga.run")
        assert run.attributes[SagaAttr.OUTCOME] == SagaAttr.COMPENSATED
        assert _steps(run) == [
            ("reserve", "completed"),
            ("wait", "no_action"),
            ("commit", "failed"),
            ("reserve", "compensated"),
        ]
        assert _status(run) == "ERROR"
        assert run.attributes[ERROR_TYPE] == "RuntimeError"

    def test_a_failed_compensation_is_recorded(self, exporter: Any) -> None:
        _run_saga(fail={"_Do:commit", "_Undo:reserve"})

        [run] = _named(exporter, "saga.run")
        assert _steps(run)[-1] == ("reserve", "compensation_failed")
        assert _status(run) == "ERROR"

    def test_an_event_triggered_saga_opens_no_run_span(self, exporter: Any) -> None:
        bus, commands, manager = _recovery_wiring(max_retries=0)
        commands.register(GiveUpOnMcpServerCommand, _Recorder())

        bus.publish(McpServerDegraded(mcp_server_id="s", consecutive_failures=1, total_failures=1, reason="test"))

        assert _named(exporter, "saga.run") == [], "an event-triggered saga holds no span between its events"
        assert manager.get_active_sagas() == []


class _Lease:
    def __init__(self) -> None:
        self.held = False

    def __call__(self) -> bool:
        return self.held


class TestDiscoveryLeaseSkip:
    def test_the_skip_is_recorded_on_transitions_only_and_never_as_a_cycle(self, exporter: Any) -> None:
        lease = _Lease()
        orchestrator = DiscoveryOrchestrator(may_manage=lease)

        for _ in range(25):
            assert orchestrator._holds_the_lease() is False
        lease.held = True
        for _ in range(3):
            assert orchestrator._holds_the_lease() is True

        transitions = _named(exporter, "discovery.lease_transition")
        assert [s.attributes[Discovery.LEASE_ROLE] for s in transitions] == [Discovery.FOLLOWER, Discovery.HOLDER]
        assert transitions[1].attributes[Discovery.SKIPPED_CYCLES] == 25
        assert all(_status(s) == "UNSET" for s in transitions), "a follower's skip is not a failure"
        assert _named(exporter, "discovery.cycle") == []

    def test_a_holder_all_along_emits_nothing(self, exporter: Any) -> None:
        lease = _Lease()
        lease.held = True
        orchestrator = DiscoveryOrchestrator(may_manage=lease)

        for _ in range(5):
            orchestrator._holds_the_lease()

        assert _named(exporter, "discovery.lease_transition") == []
