"""A failed operation must mark its span ERROR, even when the failure is handled.

The inner call handles failures as data (CallResult.success=False), so the span
never sees an exception and would otherwise stay UNSET -- a failing call would
look successful in the trace UI.

The same held for six fault barriers (#1272): each caught an operational failure,
called ``record_exception`` and carried on, and ``record_exception`` alone leaves
the status UNSET. Per barrier there are three tests. ``*_behaves_as_before``
pins what the barrier returns and does -- delivery, discovery results and
counters, the cold-start refusal -- and passes on main as it does here.
``*_keeps_its_attributes`` pins the span's existing attributes, likewise.
``*_ends_error`` is the fix: ERROR, with ``error.type`` the exception's
qualified class name. All read spans from a real SDK exporter.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.otel_sdk


def _exporter_tracer():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider


class TestMarkSpanError:
    def test_sets_error_status(self):
        from opentelemetry.trace import StatusCode

        from mcp_hangar.observability import tracing

        exporter, provider = _exporter_tracer()
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("op") as span:
            tracing.mark_span_error(span, "boom")
        assert exporter.get_finished_spans()[0].status.status_code == StatusCode.ERROR

    def test_noop_span_is_safe(self):
        # NoOpSpan (tracing disabled) must not raise.
        from mcp_hangar.observability.tracing import NoOpSpan, mark_span_error

        mark_span_error(NoOpSpan(), "boom")


class TestExecutorMarksFailureSpan:
    def _run(self, *, success: bool):
        from opentelemetry.trace import StatusCode

        import mcp_hangar.server.tools.batch.executor as ex_mod
        from mcp_hangar.server.tools.batch.executor import BatchExecutor

        exporter, provider = _exporter_tracer()
        tracer = provider.get_tracer("test")

        ex = BatchExecutor()
        call = MagicMock()
        call.tool = "divide"
        call.mcp_server = "math"
        call.call_id = "c1"
        call.metadata = None

        result = MagicMock()
        result.success = success
        result.error = None if success else "division by zero"

        with (
            patch.object(ex_mod, "get_tracer", lambda name=None: tracer),
            patch.object(ex_mod, "get_context", lambda: None),
            patch.object(ex, "_execute_call_inner", return_value=result),
        ):
            got = ex._execute_call(call, threading.Event(), 60.0, time.perf_counter())

        assert got is result
        span = next(s for s in exporter.get_finished_spans() if s.name == "batch.call.divide")
        return span.status.status_code, StatusCode

    def test_failure_marks_error(self):
        code, StatusCode = self._run(success=False)
        assert code == StatusCode.ERROR

    def test_success_stays_unset(self):
        code, StatusCode = self._run(success=True)
        assert code in (StatusCode.UNSET, StatusCode.OK)


# --- handled failures (#1272) --------------------------------------------------


class _Backend:
    """Namespaces the failures, so ``error.type`` shows a qualified name, not ``__name__``."""

    class Unavailable(OSError):
        pass

    class Crashed(RuntimeError):
        pass


UNAVAILABLE = "_Backend.Unavailable"
CRASHED = "_Backend.Crashed"


@pytest.fixture()
def sdk():
    """A local TracerProvider + InMemorySpanExporter, never registered globally."""
    exporter, provider = _exporter_tracer()
    yield exporter, provider.get_tracer("test-1272")
    exporter.clear()


def _only(exporter: Any, name: str) -> Any:
    [span] = [s for s in exporter.get_finished_spans() if s.name == name]
    return span


def _outcome(span: Any) -> tuple[str, Any]:
    """(status, error.type): ("UNSET", None) on main at every barrier."""
    return span.status.status_code.name, span.attributes.get("error.type")


def _exceptions(span: Any) -> int:
    return sum(1 for event in span.events if event.name == "exception")


def _attributes_but_outcome(span: Any) -> dict[str, Any]:
    return {k: v for k, v in span.attributes.items() if k != "error.type"}


class TestRecordHandledFailure:
    def test_records_error_status_type_and_exception(self, sdk):
        from mcp_hangar.observability.tracing import record_handled_failure

        exporter, tracer = sdk
        with tracer.start_as_current_span("op") as span:
            record_handled_failure(span, _Backend.Unavailable("down"))

        [finished] = exporter.get_finished_spans()
        assert _outcome(finished) == ("ERROR", UNAVAILABLE)
        assert _exceptions(finished) == 1

    def test_the_first_failure_names_a_shared_span_and_a_success_never_resets_it(self, sdk):
        from mcp_hangar.observability.tracing import record_handled_failure

        exporter, tracer = sdk
        with tracer.start_as_current_span("fan-out") as span:
            record_handled_failure(span, _Backend.Unavailable("first"))
            span.set_attribute("later.success", True)  # what a later handler's success does: nothing to the status
            record_handled_failure(span, _Backend.Crashed("second"))

        [finished] = exporter.get_finished_spans()
        assert _outcome(finished) == ("ERROR", UNAVAILABLE)
        assert _exceptions(finished) == 2, "every failure is still recorded as an exception event"

    def test_is_a_no_op_on_noop_spans(self):
        from opentelemetry.trace import INVALID_SPAN

        from mcp_hangar.observability.tracing import NoOpSpan, record_handled_failure

        record_handled_failure(NoOpSpan(), _Backend.Unavailable("down"))
        record_handled_failure(INVALID_SPAN, _Backend.Unavailable("down"))

    def test_is_a_no_op_without_the_sdk(self):
        from mcp_hangar.observability import tracing

        span = MagicMock()
        with patch.object(tracing, "OTEL_AVAILABLE", False):
            tracing.record_handled_failure(span, _Backend.Unavailable("down"))

        assert span.method_calls == []

    def test_never_raises(self):
        from mcp_hangar.observability.tracing import record_handled_failure

        class _Broken:
            def __getattr__(self, name: str) -> Any:
                raise RuntimeError(f"span.{name} is broken")

        record_handled_failure(_Broken(), _Backend.Unavailable("down"))


# --- event bus -----------------------------------------------------------------


def _started(server: str = "probe") -> Any:
    from mcp_hangar.domain.events import McpServerStarted

    return McpServerStarted(mcp_server_id=server, mode="subprocess", tools_count=0, startup_duration_ms=1.0)


@pytest.fixture()
def bus_tracer(sdk):
    exporter, tracer = sdk
    with patch("mcp_hangar.infrastructure.event_bus.get_tracer", return_value=tracer):
        yield exporter


def _raises(error: BaseException):
    def handler(_event: Any) -> None:
        raise error

    return handler


class _Subscriber:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.hooks: list[Any] = []

    def on_hook(self, hook: Any) -> None:
        if self.error is not None:
            raise self.error
        self.hooks.append(hook)


def _publish_with_failing_then_succeeding_handler() -> tuple[Any, list[Any]]:
    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import McpServerStarted
    from mcp_hangar.infrastructure.event_bus import EventBus

    bus = EventBus()
    seen: list[Any] = []
    bus.subscribe(McpServerStarted, _raises(_Backend.Unavailable("audit sink down")), kind=HandlerKind.EFFECT)
    bus.subscribe(McpServerStarted, seen.append, kind=HandlerKind.EFFECT)
    event = _started()
    assert bus.publish(event) is None
    return event, seen


class TestEventHandlerFailure:
    def test_behaves_as_before(self, bus_tracer):
        event, seen = _publish_with_failing_then_succeeding_handler()

        assert seen == [event], "the handler after the failing one still runs"

    def test_keeps_its_attributes(self, bus_tracer):
        _publish_with_failing_then_succeeding_handler()

        span = _only(bus_tracer, "event.publish.McpServerStarted")
        assert _attributes_but_outcome(span) == {"event.type": "McpServerStarted", "event.handlers_count": 2}

    def test_a_failing_handler_then_a_succeeding_one_ends_error(self, bus_tracer):
        _publish_with_failing_then_succeeding_handler()

        span = _only(bus_tracer, "event.publish.McpServerStarted")
        assert _outcome(span) == ("ERROR", UNAVAILABLE)


def _hook_with_failing_then_succeeding_subscriber() -> tuple[Any, _Subscriber]:
    from mcp_hangar.domain.value_objects.hook import HookPhase
    from mcp_hangar.infrastructure.event_bus import EventBus

    bus = EventBus()
    ok = _Subscriber()
    bus.subscribe_hooks(_Subscriber(_Backend.Crashed("subscriber crashed")))
    bus.subscribe_hooks(ok)
    event = _started()
    assert bus.publish_hook(event, HookPhase.REQUEST) is None
    return event, ok


class TestHookSubscriberFailure:
    def test_behaves_as_before(self, bus_tracer):
        from mcp_hangar.domain.value_objects.hook import HookPhase

        event, ok = _hook_with_failing_then_succeeding_subscriber()

        assert [(h.event, h.phase, h.sequence_number) for h in ok.hooks] == [(event, HookPhase.REQUEST, 0)]

    def test_keeps_its_attributes(self, bus_tracer):
        _hook_with_failing_then_succeeding_subscriber()

        span = _only(bus_tracer, "event.publish_hook.request")
        assert _attributes_but_outcome(span) == {"event.type": "McpServerStarted", "hook.phase": "request"}

    def test_a_failing_subscriber_then_a_succeeding_one_ends_error(self, bus_tracer):
        _hook_with_failing_then_succeeding_subscriber()

        assert _outcome(_only(bus_tracer, "event.publish_hook.request")) == ("ERROR", CRASHED)

    def test_on_the_publish_span_too(self, bus_tracer):
        # The flat publish fans out to hook subscribers inside its own span.
        from mcp_hangar.infrastructure.event_bus import EventBus

        bus = EventBus()
        ok = _Subscriber()
        bus.subscribe_hooks(_Subscriber(_Backend.Crashed("subscriber crashed")))
        bus.subscribe_hooks(ok)

        bus.publish(_started())

        assert len(ok.hooks) == 1
        assert _outcome(_only(bus_tracer, "event.publish.McpServerStarted")) == ("ERROR", CRASHED)


def _append_to_a_store_that_cannot_write() -> tuple[int, list[Any], list[Any], Any]:
    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.infrastructure.event_bus import EventBus
    from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore

    class UnwritableStore(InMemoryEventStore):
        def append(self, *args: Any, **kwargs: Any) -> int:
            raise _Backend.Unavailable("disk is gone")

    store = UnwritableStore()
    bus = EventBus(store)
    seen: list[Any] = []
    bus.subscribe_to_all(seen.append, kind=HandlerKind.EFFECT)
    events = [_started(), _started()]
    version = bus.publish_to_stream("mcp_server:probe", events, expected_version=-1)
    return version, events, seen, store


class TestEventStoreAppendFailure:
    def test_behaves_as_before(self, bus_tracer):
        version, events, seen, store = _append_to_a_store_that_cannot_write()

        assert version == -1, "the expected version comes back, as before"
        assert seen == events, "every event is still delivered"
        assert list(store.read_stream("mcp_server:probe")) == []

    def test_keeps_its_attributes(self, bus_tracer):
        _append_to_a_store_that_cannot_write()

        span = _only(bus_tracer, "event_store.append")
        assert _attributes_but_outcome(span) == {
            "event_store.stream_id": "mcp_server:probe",
            "event_store.events_count": 2,
            "event_store.expected_version": -1,
        }

    def test_the_append_span_ends_error_while_delivery_does_not(self, bus_tracer):
        _append_to_a_store_that_cannot_write()

        assert _outcome(_only(bus_tracer, "event_store.append")) == ("ERROR", UNAVAILABLE)
        delivered = [s for s in bus_tracer.get_finished_spans() if s.name == "event.publish.McpServerStarted"]
        assert [_outcome(s) for s in delivered] == [("UNSET", None), ("UNSET", None)]


# --- discovery -----------------------------------------------------------------


class _RecordingBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, event: Any) -> None:
        self.published.append(event)


@pytest.fixture()
def discovery_tracer(sdk):
    exporter, tracer = sdk
    with patch("mcp_hangar.application.discovery.discovery_orchestrator.get_tracer", return_value=tracer):
        yield exporter


def _orchestrator(bus: _RecordingBus | None = None) -> Any:
    from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator

    config = DiscoveryConfig()
    config.security.require_health_check = False
    return DiscoveryOrchestrator(config=config, event_bus=bus)


def _orchestrator_errors(error_type: str) -> float:
    from mcp_hangar.metrics import DISCOVERY_ERRORS_TOTAL

    labels = {"source_type": "orchestrator", "error_type": error_type}
    return sum(s.value for s in DISCOVERY_ERRORS_TOTAL.collect() if s.labels == labels)


def _failing_cycle() -> tuple[Any, float]:
    orchestrator = _orchestrator()
    orchestrator._discovery_service.run_discovery_cycle = AsyncMock(side_effect=_Backend.Unavailable("source down"))
    before = _orchestrator_errors("Unavailable")
    result = asyncio.run(orchestrator.run_discovery_cycle())
    return result, _orchestrator_errors("Unavailable") - before


class TestDiscoveryCycleFailure:
    def test_behaves_as_before(self, discovery_tracer):
        result, counted = _failing_cycle()

        assert (
            result.discovered_count,
            result.registered_count,
            result.updated_count,
            result.quarantined_count,
            result.deregistered_count,
            result.error_count,
            result.source_results,
        ) == (0, 0, 0, 0, 0, 1, {})
        assert counted == 1

    def test_keeps_its_attributes(self, discovery_tracer):
        _failing_cycle()

        attributes = _attributes_but_outcome(_only(discovery_tracer, "discovery.cycle"))
        assert set(attributes) == {
            "discovery.registered_count",
            "discovery.quarantined_count",
            "discovery.error_count",
            "discovery.duration_ms",
        }
        assert (attributes["discovery.registered_count"], attributes["discovery.error_count"]) == (0, 1)

    def test_ends_error(self, discovery_tracer):
        _failing_cycle()

        assert _outcome(_only(discovery_tracer, "discovery.cycle")) == ("ERROR", UNAVAILABLE)


def _discovered() -> Any:
    from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer

    return DiscoveredMcpServer.create(
        name="probe",
        source_type="docker",
        mode="http",
        connection_info={"host": "10.88.0.7", "port": 8080},
        metadata={"runtime_addresses": ["10.88.0.7"]},
    )


def _register_with(on_register: Any) -> tuple[str, Any, _RecordingBus]:
    bus = _RecordingBus()
    orchestrator = _orchestrator(bus)
    orchestrator.on_register = on_register
    return asyncio.run(orchestrator._process_mcp_server(_discovered())), orchestrator, bus


async def _unreachable_control_plane(_server: Any) -> bool:
    raise _Backend.Unavailable("control plane unreachable")


async def _control_plane_refuses(_server: Any) -> bool:
    return False


class TestRegistrationFailure:
    def test_behaves_as_before(self, discovery_tracer):
        from mcp_hangar.domain.events import McpServerDiscovered

        outcome, orchestrator, bus = _register_with(_unreachable_control_plane)

        assert outcome == "skipped"
        assert orchestrator._lifecycle_manager.get_mcp_server("probe") is None
        assert [type(e) for e in bus.published] == [McpServerDiscovered]

    def test_keeps_its_attributes(self, discovery_tracer):
        _register_with(_unreachable_control_plane)

        span = _only(discovery_tracer, "discovery.process_mcp_server")
        assert _attributes_but_outcome(span) == {
            "discovery.mcp_server_name": "probe",
            "discovery.source_type": "docker",
            "discovery.validation_passed": True,
            "discovery.result": "skipped",
        }

    def test_ends_error(self, discovery_tracer):
        _register_with(_unreachable_control_plane)

        assert _outcome(_only(discovery_tracer, "discovery.process_mcp_server")) == ("ERROR", UNAVAILABLE)

    def test_a_control_plane_refusal_is_an_answer_not_an_error(self, discovery_tracer):
        # Same result, no exception: correct enforcement must not read as a failure.
        outcome, _orchestrator_, _bus = _register_with(_control_plane_refuses)

        span = _only(discovery_tracer, "discovery.process_mcp_server")
        assert outcome == "skipped"
        assert span.attributes["discovery.result"] == "skipped"
        assert _outcome(span) == ("UNSET", None)


# --- batch cold start ------------------------------------------------------------


def _cold_start(tracer: Any, send: Any) -> tuple[Any, MagicMock]:
    from mcp_hangar.server.tools.batch.executor import BatchExecutor, _CallPipeline
    from mcp_hangar.server.tools.batch.models import CallSpec

    ctx = MagicMock()
    ctx.command_bus.send.side_effect = send
    now = time.perf_counter()
    pipeline = _CallPipeline(
        call=CallSpec(index=0, call_id="c-1272", mcp_server="math", tool="add", arguments={}),
        ctx=ctx,
        call_start=now,
        cancel_event=threading.Event(),
        global_timeout=30.0,
        batch_start_time=now,
        caller_tenant_id=None,
        resolver=None,
        proj_registry=None,
        tracer=tracer,
    )
    pipeline.mcp_server_obj = SimpleNamespace(state=SimpleNamespace(value="cold"))
    pipeline.target_server_id = "math"
    return BatchExecutor()._gate_cold_start(pipeline), ctx.command_bus.send


def _start_error() -> Any:
    from mcp_hangar.domain.exceptions import McpServerStartError

    return McpServerStartError("math", "process exited with code 1")


class TestColdStartFailure:
    def test_behaves_as_before(self, sdk):
        from mcp_hangar.application.commands import StartMcpServerCommand

        _exporter, tracer = sdk
        error = _start_error()

        refusal, send = _cold_start(tracer, error)

        assert (refusal.index, refusal.call_id, refusal.success, refusal.error, refusal.error_type, refusal.result) == (
            0,
            "c-1272",
            False,
            f"Failed to start mcp_server: {error}",
            "McpServerStartError",
            None,
        )
        [command] = [c.args[0] for c in send.call_args_list]
        assert isinstance(command, StartMcpServerCommand) and command.mcp_server_id == "math"

    def test_keeps_its_attributes(self, sdk):
        exporter, tracer = sdk

        _cold_start(tracer, _start_error())

        span = _only(exporter, "mcp_server.cold_start")
        assert _attributes_but_outcome(span) == {"mcp.server.id": "math", "cold_start.result": "error"}

    def test_ends_error(self, sdk):
        exporter, tracer = sdk

        _cold_start(tracer, _start_error())

        assert _outcome(_only(exporter, "mcp_server.cold_start")) == ("ERROR", "McpServerStartError")

    def test_a_successful_start_stays_unset(self, sdk):
        exporter, tracer = sdk

        refusal, _send = _cold_start(tracer, lambda _command: None)

        span = _only(exporter, "mcp_server.cold_start")
        assert refusal is None
        assert span.attributes["cold_start.result"] == "success"
        assert _outcome(span) == ("UNSET", None)
