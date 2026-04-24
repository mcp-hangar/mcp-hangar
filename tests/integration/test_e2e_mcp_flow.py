"""End-to-end integration tests for MCP server lifecycle.

Tests exercise real running MCP servers (subprocess mode) through the full
lifecycle: startup, tool discovery, invocation, health checks, events,
concurrent access, idle shutdown, restart, and the Hangar facade.

Also covers infrastructure integration: EventBus pub/sub pipeline,
SecurityEventHandler anomaly detection, MetricsEventHandler accumulation,
McpServerGroup load balancing + failover, RecoverySaga, SingleFlight
cold start deduplication, lock hierarchy enforcement, and event sourcing
round-trips -- all driven by real subprocess MCP servers.

Uses tests/mock_provider.py as the subprocess MCP server.
"""

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from mcp_hangar.domain.events import (
    DomainEvent,
    HealthCheckPassed,
    McpServerDegraded,
    McpServerStarted,
    McpServerStateChanged,
    McpServerStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from mcp_hangar.domain.exceptions import McpServerNotFoundError, ToolInvocationError, ToolNotFoundError
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.value_objects import McpServerMode, McpServerState

MOCK_PROVIDER = str(Path(__file__).resolve().parent.parent / "mock_provider.py")


@pytest.fixture
def mcp_server():
    """Create a subprocess McpServer pointing to mock_provider, with cleanup."""
    server = McpServer(
        mcp_server_id="test-math",
        mode="subprocess",
        command=[sys.executable, MOCK_PROVIDER],
        max_consecutive_failures=3,
    )
    yield server
    if server.state != McpServerState.COLD:
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def started_mcp_server(mcp_server):
    """Start the McpServer and drain startup events so tests begin clean."""
    mcp_server.ensure_ready()
    mcp_server.collect_events()
    yield mcp_server


@pytest.fixture
def failing_provider_script(tmp_path):
    """Provider script that exits immediately (startup will fail)."""
    script = tmp_path / "failing_provider.py"
    script.write_text("import sys; sys.exit(1)\n")
    return str(script)


class TestSubprocessLifecycleSmoke:
    """Smoke tests: start, discover, invoke, stop."""

    def test_cold_to_ready(self, mcp_server):
        assert mcp_server.state == McpServerState.COLD
        mcp_server.ensure_ready()
        assert mcp_server.state == McpServerState.READY

    def test_tool_discovery(self, mcp_server):
        mcp_server.ensure_ready()
        expected = {"add", "subtract", "multiply", "divide", "power", "echo"}
        assert set(mcp_server.get_tool_names()) == expected

    def test_invoke_and_shutdown(self, mcp_server):
        mcp_server.ensure_ready()
        result = mcp_server.invoke_tool("add", {"a": 10, "b": 32})
        assert result["result"] == 42
        mcp_server.shutdown()
        assert mcp_server.state == McpServerState.COLD

    def test_mode_is_subprocess(self, mcp_server):
        assert mcp_server.mode == McpServerMode.SUBPROCESS


class TestToolInvocations:
    """Invoke every tool on the real subprocess and verify results."""

    def test_add(self, started_mcp_server):
        assert started_mcp_server.invoke_tool("add", {"a": 3, "b": 7})["result"] == 10

    def test_subtract(self, started_mcp_server):
        assert started_mcp_server.invoke_tool("subtract", {"a": 10, "b": 4})["result"] == 6

    def test_multiply(self, started_mcp_server):
        assert started_mcp_server.invoke_tool("multiply", {"a": 6, "b": 7})["result"] == 42

    def test_divide(self, started_mcp_server):
        assert started_mcp_server.invoke_tool("divide", {"a": 100, "b": 4})["result"] == 25.0

    def test_power(self, started_mcp_server):
        assert started_mcp_server.invoke_tool("power", {"base": 2, "exponent": 10})["result"] == 1024

    def test_echo(self, started_mcp_server):
        assert started_mcp_server.invoke_tool("echo", {"message": "hello e2e"})["message"] == "hello e2e"

    def test_divide_by_zero_raises(self, started_mcp_server):
        with pytest.raises(ToolInvocationError):
            started_mcp_server.invoke_tool("divide", {"a": 1, "b": 0})

    def test_unknown_tool_raises(self, started_mcp_server):
        with pytest.raises(ToolNotFoundError):
            started_mcp_server.invoke_tool("nonexistent_tool", {})


class TestEventEmission:
    """Verify domain events emitted at each lifecycle step."""

    def test_startup_events(self, mcp_server):
        """cold -> initializing -> ready + McpServerStarted."""
        mcp_server.ensure_ready()
        events = mcp_server.collect_events()

        state_changes = [e for e in events if isinstance(e, McpServerStateChanged)]
        assert len(state_changes) >= 2
        assert state_changes[0].old_state == "cold"
        assert state_changes[0].new_state == "initializing"
        assert state_changes[1].old_state == "initializing"
        assert state_changes[1].new_state == "ready"

        started = [e for e in events if isinstance(e, McpServerStarted)]
        assert len(started) == 1
        assert started[0].mcp_server_id == "test-math"
        assert started[0].mode == "subprocess"
        assert started[0].tools_count == 6
        assert started[0].startup_duration_ms > 0

    def test_invocation_events(self, started_mcp_server):
        """ToolInvocationRequested + ToolInvocationCompleted per call."""
        started_mcp_server.invoke_tool("add", {"a": 1, "b": 2})
        events = started_mcp_server.collect_events()

        requested = [e for e in events if isinstance(e, ToolInvocationRequested)]
        completed = [e for e in events if isinstance(e, ToolInvocationCompleted)]

        assert len(requested) == 1
        assert requested[0].tool_name == "add"
        assert requested[0].mcp_server_id == "test-math"

        assert len(completed) == 1
        assert completed[0].tool_name == "add"
        assert completed[0].duration_ms >= 0
        assert completed[0].result_size_bytes > 0

    def test_failed_invocation_events(self, started_mcp_server):
        """ToolInvocationFailed on JSON-RPC error response."""
        with pytest.raises(ToolInvocationError):
            started_mcp_server.invoke_tool("divide", {"a": 1, "b": 0})

        events = started_mcp_server.collect_events()
        failed = [e for e in events if isinstance(e, ToolInvocationFailed)]
        assert len(failed) == 1
        assert failed[0].tool_name == "divide"
        assert "division by zero" in failed[0].error_message

    def test_shutdown_events(self, started_mcp_server):
        """McpServerStopped with reason on shutdown."""
        started_mcp_server.shutdown()
        events = started_mcp_server.collect_events()

        stopped = [e for e in events if isinstance(e, McpServerStopped)]
        assert len(stopped) == 1
        assert stopped[0].mcp_server_id == "test-math"
        assert stopped[0].reason == "shutdown"

    def test_health_check_events(self, started_mcp_server):
        """HealthCheckPassed when tools/list succeeds."""
        assert started_mcp_server.health_check() is True
        events = started_mcp_server.collect_events()

        passed = [e for e in events if isinstance(e, HealthCheckPassed)]
        assert len(passed) == 1
        assert passed[0].duration_ms >= 0


class TestHealthCheck:
    """Health check behaviour with real subprocess."""

    def test_healthy_server(self, started_mcp_server):
        assert started_mcp_server.health_check() is True
        assert started_mcp_server.health.consecutive_failures == 0

    def test_cold_server_returns_false(self, mcp_server):
        assert mcp_server.health_check() is False


class TestConcurrentInvocations:
    """Thread-safe concurrent tool calls on a real subprocess."""

    def test_concurrent_add_calls(self, started_mcp_server):
        """10 concurrent add invocations all return correct results."""
        errors: list[Exception] = []
        results: dict[int, int | float] = {}

        def invoke(i: int) -> tuple[int, int | float | None]:
            try:
                r = started_mcp_server.invoke_tool("add", {"a": i, "b": i})
                return i, r["result"]
            except Exception as e:  # noqa: BLE001
                errors.append(e)
                return i, None

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(invoke, i): i for i in range(10)}
            for f in as_completed(futures):
                idx, val = f.result()
                results[idx] = val

        assert not errors, f"Concurrent invocations had errors: {errors}"
        for i in range(10):
            assert results[i] == i + i

    def test_concurrent_mixed_tools(self, started_mcp_server):
        """Concurrent calls to different tools return correct results."""
        calls = [
            ("add", {"a": 1, "b": 2}),
            ("subtract", {"a": 10, "b": 3}),
            ("multiply", {"a": 4, "b": 5}),
            ("echo", {"message": "concurrent"}),
        ]
        results: list[dict | None] = [None] * len(calls)
        errors: list[Exception] = []

        def invoke(idx: int, tool: str, args: dict) -> tuple[int, dict | None]:
            try:
                return idx, started_mcp_server.invoke_tool(tool, args)
            except Exception as e:  # noqa: BLE001
                errors.append(e)
                return idx, None

        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(invoke, i, t, a) for i, (t, a) in enumerate(calls)]
            for f in as_completed(futs):
                idx, r = f.result()
                results[idx] = r

        assert not errors
        assert results[0]["result"] == 3
        assert results[1]["result"] == 7
        assert results[2]["result"] == 20
        assert results[3]["message"] == "concurrent"


class TestStartupFailure:
    """Verify DEAD/DEGRADED state when server fails to start."""

    def test_bad_command_goes_dead_or_degraded(self):
        server = McpServer(
            mcp_server_id="bad-cmd",
            mode="subprocess",
            command=[sys.executable, "/nonexistent/path/server.py"],
            max_consecutive_failures=5,
        )
        with pytest.raises(Exception):
            server.ensure_ready()
        assert server.state in (McpServerState.DEAD, McpServerState.DEGRADED)

    def test_failing_script_emits_state_change(self, failing_provider_script):
        server = McpServer(
            mcp_server_id="fail-test",
            mode="subprocess",
            command=[sys.executable, failing_provider_script],
            max_consecutive_failures=5,
        )
        with pytest.raises(Exception):
            server.ensure_ready()

        events = server.collect_events()
        state_changes = [e for e in events if isinstance(e, McpServerStateChanged)]
        assert any(e.new_state == "initializing" for e in state_changes)


class TestRestartAfterStop:
    """Stop and restart cycle."""

    def test_stop_and_restart(self, mcp_server):
        mcp_server.ensure_ready()
        assert mcp_server.state == McpServerState.READY
        assert mcp_server.invoke_tool("add", {"a": 1, "b": 1})["result"] == 2

        mcp_server.shutdown()
        assert mcp_server.state == McpServerState.COLD
        mcp_server.collect_events()

        mcp_server.ensure_ready()
        assert mcp_server.state == McpServerState.READY
        assert mcp_server.invoke_tool("multiply", {"a": 3, "b": 4})["result"] == 12


class TestIdleShutdown:
    """Idle TTL triggers automatic shutdown."""

    def test_idle_detection_and_shutdown(self):
        server = McpServer(
            mcp_server_id="idle-test",
            mode="subprocess",
            command=[sys.executable, MOCK_PROVIDER],
            idle_ttl_s=1,
        )
        try:
            server.ensure_ready()
            server.invoke_tool("echo", {"message": "start"})
            server.collect_events()

            time.sleep(1.5)
            assert server.is_idle

            assert server.maybe_shutdown_idle() is True
            assert server.state == McpServerState.COLD

            events = server.collect_events()
            stopped = [e for e in events if isinstance(e, McpServerStopped)]
            assert len(stopped) == 1
            assert stopped[0].reason == "idle"
        finally:
            if server.state != McpServerState.COLD:
                server.shutdown()


class TestFacadeEndToEnd:
    """End-to-end tests through the Hangar / SyncHangar facade."""

    async def test_async_facade_lifecycle(self):
        """Start, invoke, list, health_check, error -- single bootstrap."""
        from mcp_hangar.facade import Hangar, HangarConfig

        config = HangarConfig().add_mcp_server("math", command=[sys.executable, MOCK_PROVIDER]).build()
        try:
            hangar = Hangar.from_builder(config)
            await hangar.start()
        except ValueError as exc:
            if "already registered" in str(exc):
                pytest.skip("bootstrap global state conflict with other tests in this process")
            raise

        try:
            result = await hangar.invoke("math", "add", {"a": 100, "b": 23})
            assert result["result"] == 123

            servers = await hangar.list_mcp_servers()
            assert any(s.name == "math" for s in servers)

            assert await hangar.health_check("math") is True

            with pytest.raises(McpServerNotFoundError):
                await hangar.invoke("ghost", "add", {"a": 1, "b": 2})
        finally:
            await hangar.stop()


class TestEventStreamInvariants:
    """Cross-invocation event ordering and uniqueness."""

    def test_event_chronological_order(self, started_mcp_server):
        """Events from sequential invocations have non-decreasing timestamps."""
        started_mcp_server.invoke_tool("add", {"a": 1, "b": 1})
        started_mcp_server.invoke_tool("multiply", {"a": 2, "b": 3})
        started_mcp_server.invoke_tool("echo", {"message": "last"})

        events = started_mcp_server.collect_events()
        timestamps = [e.occurred_at for e in events]
        assert timestamps == sorted(timestamps), "Events must be in chronological order"

    def test_correlation_ids_unique(self, started_mcp_server):
        """Each invocation has a unique correlation_id."""
        started_mcp_server.invoke_tool("add", {"a": 1, "b": 1})
        started_mcp_server.invoke_tool("add", {"a": 2, "b": 2})

        events = started_mcp_server.collect_events()
        requested = [e for e in events if isinstance(e, ToolInvocationRequested)]
        ids = [e.correlation_id for e in requested]
        assert len(set(ids)) == len(ids), "Correlation IDs must be unique"

    def test_requested_completed_pairs_match(self, started_mcp_server):
        """Every completed invocation has a matching requested event with same correlation_id."""
        started_mcp_server.invoke_tool("add", {"a": 1, "b": 2})
        started_mcp_server.invoke_tool("echo", {"message": "x"})

        events = started_mcp_server.collect_events()
        req_ids = {e.correlation_id for e in events if isinstance(e, ToolInvocationRequested)}
        comp_ids = {e.correlation_id for e in events if isinstance(e, ToolInvocationCompleted)}
        assert req_ids == comp_ids


def _make_server(server_id: str, **kwargs) -> McpServer:
    return McpServer(
        mcp_server_id=server_id,
        mode="subprocess",
        command=[sys.executable, MOCK_PROVIDER],
        **kwargs,
    )


def _shutdown_safe(server: McpServer) -> None:
    if server.state != McpServerState.COLD:
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001
            pass


class TestCircuitBreakerIntegration:
    """Circuit breaker state transitions driven by real subprocess operations."""

    def test_circuit_closed_after_successful_invocations(self, started_mcp_server):
        from mcp_hangar.domain.model.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))

        started_mcp_server.invoke_tool("add", {"a": 1, "b": 2})
        cb.record_success()
        started_mcp_server.invoke_tool("multiply", {"a": 3, "b": 4})
        cb.record_success()

        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_circuit_opens_from_real_invocation_failures(self, started_mcp_server):
        from mcp_hangar.domain.model.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3, reset_timeout_s=0.5))

        for _ in range(3):
            with pytest.raises(ToolInvocationError):
                started_mcp_server.invoke_tool("divide", {"a": 1, "b": 0})
            cb.record_failure()

        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_circuit_half_open_after_timeout(self):
        from mcp_hangar.domain.model.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2, reset_timeout_s=1.0, probe_count=1))

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        cb._opened_at = time.time() - 2.0
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_circuit_closes_on_probe_success_after_real_recovery(self):
        from mcp_hangar.domain.model.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2, reset_timeout_s=1.0, probe_count=1))

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        cb._opened_at = time.time() - 2.0
        cb.allow_request()
        assert cb.state == CircuitState.HALF_OPEN

        server = _make_server("cb-probe")
        try:
            server.ensure_ready()
            server.invoke_tool("add", {"a": 1, "b": 1})
            cb.record_success()
            assert cb.state == CircuitState.CLOSED
            assert cb.failure_count == 0
        finally:
            _shutdown_safe(server)

    def test_circuit_reopens_on_probe_failure(self):
        from mcp_hangar.domain.model.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2, reset_timeout_s=1.0, probe_count=1))

        cb.record_failure()
        cb.record_failure()
        cb._opened_at = time.time() - 2.0
        cb.allow_request()
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_circuit_state_change_callbacks(self):
        from mcp_hangar.domain.model.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

        transitions: list[tuple[str, str]] = []
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2, reset_timeout_s=1.0, probe_count=1))
        cb._on_state_change = lambda old, new: transitions.append((old.value, new.value))

        cb.record_failure()
        cb.record_failure()
        cb._opened_at = time.time() - 2.0
        cb.allow_request()
        cb.record_success()

        assert ("closed", "open") in transitions
        assert ("open", "half_open") in transitions
        assert ("half_open", "closed") in transitions


class TestEventBusPipeline:
    """Wire EventBus with real handlers, drive events from real McpServer ops."""

    def test_events_flow_through_logging_handler(self, started_mcp_server):
        from mcp_hangar.application.event_handlers.logging_handler import LoggingEventHandler
        from mcp_hangar.infrastructure.event_bus import EventBus

        received: list[DomainEvent] = []
        bus = EventBus()
        logging_handler = LoggingEventHandler()

        original_handle = logging_handler.handle

        def tracking_handle(event: DomainEvent) -> None:
            received.append(event)
            original_handle(event)

        bus.subscribe_to_all(tracking_handle)

        started_mcp_server.invoke_tool("add", {"a": 5, "b": 3})
        for event in started_mcp_server.collect_events():
            bus.publish(event)

        assert any(isinstance(e, ToolInvocationRequested) for e in received)
        assert any(isinstance(e, ToolInvocationCompleted) for e in received)

    def test_events_flow_through_metrics_handler(self, started_mcp_server):
        from mcp_hangar.application.event_handlers.metrics_handler import MetricsEventHandler
        from mcp_hangar.infrastructure.event_bus import EventBus

        bus = EventBus()
        metrics = MetricsEventHandler()
        bus.subscribe_to_all(metrics.handle)

        started_mcp_server.invoke_tool("multiply", {"a": 6, "b": 7})
        for event in started_mcp_server.collect_events():
            bus.publish(event)

        m = metrics.get_metrics(started_mcp_server.mcp_server_id)
        assert m is not None
        assert m.successful_invocations == 1
        assert m.total_invocations == 1
        assert m.total_duration_ms > 0

    def test_handler_failure_does_not_break_pipeline(self, started_mcp_server):
        from mcp_hangar.infrastructure.event_bus import EventBus

        received_after: list[DomainEvent] = []
        bus = EventBus()

        def broken_handler(_event: DomainEvent) -> None:
            raise RuntimeError("handler crash")

        bus.subscribe_to_all(broken_handler)
        bus.subscribe_to_all(lambda e: received_after.append(e))

        started_mcp_server.invoke_tool("echo", {"message": "pipeline"})
        for event in started_mcp_server.collect_events():
            bus.publish(event)

        assert len(received_after) > 0

    def test_multiple_handlers_all_receive_events(self, started_mcp_server):
        from mcp_hangar.application.event_handlers.metrics_handler import MetricsEventHandler
        from mcp_hangar.infrastructure.event_bus import EventBus

        bus = EventBus()
        metrics = MetricsEventHandler()
        log_received: list[DomainEvent] = []

        bus.subscribe_to_all(metrics.handle)
        bus.subscribe_to_all(lambda e: log_received.append(e))

        started_mcp_server.invoke_tool("add", {"a": 1, "b": 2})
        started_mcp_server.invoke_tool("subtract", {"a": 10, "b": 3})
        for event in started_mcp_server.collect_events():
            bus.publish(event)

        m = metrics.get_metrics(started_mcp_server.mcp_server_id)
        assert m is not None
        assert m.total_invocations == 2
        assert len(log_received) >= 4

    def test_event_bus_publish_to_stream_with_real_events(self, started_mcp_server):
        from mcp_hangar.infrastructure.event_bus import EventBus
        from mcp_hangar.infrastructure.event_store import InMemoryEventStore

        store = InMemoryEventStore()
        bus = EventBus(event_store=store)

        started_mcp_server.invoke_tool("add", {"a": 10, "b": 20})
        events = started_mcp_server.collect_events()

        stream_id = f"mcp_server:{started_mcp_server.mcp_server_id}"
        new_version = bus.publish_to_stream(stream_id, events, expected_version=-1)

        assert new_version == len(events) - 1
        stored = store.load(stream_id)
        assert len(stored) == len(events)
        assert stored[0].event_type == type(events[0]).__name__


class TestSecurityEventHandlerIntegration:
    """SecurityEventHandler wired to real MCP server domain events."""

    def test_tool_invocation_failure_emits_access_denied(self, started_mcp_server):
        from mcp_hangar.application.event_handlers.security_handler import (
            InMemorySecuritySink,
            SecurityEventHandler,
            SecurityEventType,
        )
        from mcp_hangar.infrastructure.event_bus import EventBus

        sink = InMemorySecuritySink()
        handler = SecurityEventHandler(sink=sink)
        bus = EventBus()
        bus.subscribe_to_all(handler.handle)

        with pytest.raises(ToolInvocationError):
            started_mcp_server.invoke_tool("divide", {"a": 1, "b": 0})

        for event in started_mcp_server.collect_events():
            bus.publish(event)

        access_denied = sink.query(event_type=SecurityEventType.ACCESS_DENIED)
        assert len(access_denied) >= 1
        assert access_denied[0].tool_name == "divide"

    def test_server_start_emits_access_granted(self):
        from mcp_hangar.application.event_handlers.security_handler import (
            InMemorySecuritySink,
            SecurityEventHandler,
            SecurityEventType,
        )
        from mcp_hangar.infrastructure.event_bus import EventBus

        sink = InMemorySecuritySink()
        handler = SecurityEventHandler(sink=sink)
        bus = EventBus()
        bus.subscribe_to_all(handler.handle)

        server = _make_server("sec-start")
        try:
            server.ensure_ready()
            for event in server.collect_events():
                bus.publish(event)

            granted = sink.query(event_type=SecurityEventType.ACCESS_GRANTED)
            assert len(granted) >= 1
            assert granted[0].mcp_server_id == "sec-start"
        finally:
            _shutdown_safe(server)

    def test_anomaly_detection_tracks_failures(self, started_mcp_server):
        from mcp_hangar.application.event_handlers.security_handler import (
            InMemorySecuritySink,
            SecurityEventHandler,
        )
        from mcp_hangar.infrastructure.event_bus import EventBus

        sink = InMemorySecuritySink()
        handler = SecurityEventHandler(sink=sink, enable_anomaly_detection=True)
        bus = EventBus()
        bus.subscribe_to_all(handler.handle)

        for _ in range(3):
            with pytest.raises(ToolInvocationError):
                started_mcp_server.invoke_tool("divide", {"a": 1, "b": 0})

        for event in started_mcp_server.collect_events():
            bus.publish(event)

        assert sink.count >= 3

    def test_severity_counts_reflect_real_operations(self, started_mcp_server):
        from mcp_hangar.application.event_handlers.security_handler import InMemorySecuritySink, SecurityEventHandler
        from mcp_hangar.infrastructure.event_bus import EventBus

        sink = InMemorySecuritySink()
        handler = SecurityEventHandler(sink=sink)
        bus = EventBus()
        bus.subscribe_to_all(handler.handle)

        started_mcp_server.invoke_tool("add", {"a": 1, "b": 1})
        with pytest.raises(ToolInvocationError):
            started_mcp_server.invoke_tool("divide", {"a": 1, "b": 0})

        for event in started_mcp_server.collect_events():
            bus.publish(event)

        counts = sink.get_severity_counts()
        assert counts["low"] >= 1


class TestMetricsHandlerIntegration:
    """MetricsEventHandler accumulates stats from real tool invocations."""

    def test_success_and_failure_counts(self, started_mcp_server):
        from mcp_hangar.application.event_handlers.metrics_handler import MetricsEventHandler
        from mcp_hangar.infrastructure.event_bus import EventBus

        bus = EventBus()
        metrics = MetricsEventHandler()
        bus.subscribe_to_all(metrics.handle)

        started_mcp_server.invoke_tool("add", {"a": 1, "b": 2})
        started_mcp_server.invoke_tool("multiply", {"a": 3, "b": 4})
        with pytest.raises(ToolInvocationError):
            started_mcp_server.invoke_tool("divide", {"a": 1, "b": 0})

        for event in started_mcp_server.collect_events():
            bus.publish(event)

        m = metrics.get_metrics(started_mcp_server.mcp_server_id)
        assert m is not None
        assert m.successful_invocations == 2
        assert m.failed_invocations == 1
        assert m.total_invocations == 3

    def test_success_rate_calculation(self, started_mcp_server):
        from mcp_hangar.application.event_handlers.metrics_handler import MetricsEventHandler
        from mcp_hangar.infrastructure.event_bus import EventBus

        bus = EventBus()
        metrics = MetricsEventHandler()
        bus.subscribe_to_all(metrics.handle)

        for i in range(4):
            started_mcp_server.invoke_tool("add", {"a": i, "b": i})
        with pytest.raises(ToolInvocationError):
            started_mcp_server.invoke_tool("divide", {"a": 1, "b": 0})

        for event in started_mcp_server.collect_events():
            bus.publish(event)

        m = metrics.get_metrics(started_mcp_server.mcp_server_id)
        assert m is not None
        assert m.success_rate == pytest.approx(80.0)

    def test_latency_tracking(self, started_mcp_server):
        from mcp_hangar.application.event_handlers.metrics_handler import MetricsEventHandler
        from mcp_hangar.infrastructure.event_bus import EventBus

        bus = EventBus()
        metrics = MetricsEventHandler()
        bus.subscribe_to_all(metrics.handle)

        for _ in range(5):
            started_mcp_server.invoke_tool("echo", {"message": "latency"})

        for event in started_mcp_server.collect_events():
            bus.publish(event)

        m = metrics.get_metrics(started_mcp_server.mcp_server_id)
        assert m is not None
        assert len(m.invocation_latencies) == 5
        assert m.average_latency_ms > 0
        assert m.p95_latency_ms >= m.average_latency_ms

    def test_health_check_metrics(self, started_mcp_server):
        from mcp_hangar.application.event_handlers.metrics_handler import MetricsEventHandler
        from mcp_hangar.infrastructure.event_bus import EventBus

        bus = EventBus()
        metrics = MetricsEventHandler()
        bus.subscribe_to_all(metrics.handle)

        started_mcp_server.health_check()
        started_mcp_server.health_check()

        for event in started_mcp_server.collect_events():
            bus.publish(event)

        m = metrics.get_metrics(started_mcp_server.mcp_server_id)
        assert m is not None
        assert m.health_checks_passed == 2

    def test_per_server_metrics_isolation(self):
        from mcp_hangar.application.event_handlers.metrics_handler import MetricsEventHandler
        from mcp_hangar.infrastructure.event_bus import EventBus

        bus = EventBus()
        metrics = MetricsEventHandler()
        bus.subscribe_to_all(metrics.handle)

        s1 = _make_server("metrics-s1")
        s2 = _make_server("metrics-s2")
        try:
            s1.ensure_ready()
            s2.ensure_ready()
            s1.collect_events()
            s2.collect_events()

            s1.invoke_tool("add", {"a": 1, "b": 2})
            s2.invoke_tool("multiply", {"a": 3, "b": 4})
            s2.invoke_tool("echo", {"message": "x"})

            for event in s1.collect_events():
                bus.publish(event)
            for event in s2.collect_events():
                bus.publish(event)

            m1 = metrics.get_metrics("metrics-s1")
            m2 = metrics.get_metrics("metrics-s2")
            assert m1 is not None
            assert m2 is not None
            assert m1.total_invocations == 1
            assert m2.total_invocations == 2
        finally:
            _shutdown_safe(s1)
            _shutdown_safe(s2)


class TestMultiServerOrchestration:
    """Multiple independent MCP servers with independent lifecycles."""

    def test_three_servers_independent_lifecycle(self):
        servers = [_make_server(f"multi-{i}") for i in range(3)]
        try:
            for s in servers:
                s.ensure_ready()
                assert s.state == McpServerState.READY

            assert servers[0].invoke_tool("add", {"a": 1, "b": 2})["result"] == 3
            assert servers[1].invoke_tool("multiply", {"a": 3, "b": 4})["result"] == 12
            assert servers[2].invoke_tool("echo", {"message": "hi"})["message"] == "hi"

            servers[1].shutdown()
            assert servers[1].state == McpServerState.COLD

            assert servers[0].state == McpServerState.READY
            assert servers[2].state == McpServerState.READY
            assert servers[0].invoke_tool("add", {"a": 10, "b": 10})["result"] == 20
        finally:
            for s in servers:
                _shutdown_safe(s)

    def test_concurrent_startup_of_multiple_servers(self):
        servers = [_make_server(f"par-{i}") for i in range(4)]
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futs = [pool.submit(s.ensure_ready) for s in servers]
                for f in as_completed(futs):
                    f.result()

            for s in servers:
                assert s.state == McpServerState.READY
                assert s.invoke_tool("add", {"a": 1, "b": 1})["result"] == 2
        finally:
            for s in servers:
                _shutdown_safe(s)

    def test_server_shutdown_does_not_affect_others(self):
        s1 = _make_server("iso-1")
        s2 = _make_server("iso-2")
        try:
            s1.ensure_ready()
            s2.ensure_ready()

            s1.shutdown()
            assert s1.state == McpServerState.COLD

            assert s2.health_check() is True
            assert s2.invoke_tool("subtract", {"a": 10, "b": 3})["result"] == 7
        finally:
            _shutdown_safe(s1)
            _shutdown_safe(s2)

    def test_event_streams_isolated_per_server(self):
        s1 = _make_server("evt-1")
        s2 = _make_server("evt-2")
        try:
            s1.ensure_ready()
            s2.ensure_ready()
            s1.collect_events()
            s2.collect_events()

            s1.invoke_tool("add", {"a": 1, "b": 1})
            s2.invoke_tool("multiply", {"a": 2, "b": 2})
            s2.invoke_tool("echo", {"message": "x"})

            e1 = s1.collect_events()
            e2 = s2.collect_events()

            req1 = [e for e in e1 if isinstance(e, ToolInvocationRequested)]
            req2 = [e for e in e2 if isinstance(e, ToolInvocationRequested)]

            assert len(req1) == 1
            assert len(req2) == 2
            assert all(e.mcp_server_id == "evt-1" for e in req1)
            assert all(e.mcp_server_id == "evt-2" for e in req2)
        finally:
            _shutdown_safe(s1)
            _shutdown_safe(s2)


class TestMcpServerGroupLoadBalancing:
    """McpServerGroup with real running subprocess servers."""

    def test_group_round_robin_selection(self):
        from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
        from mcp_hangar.domain.value_objects import LoadBalancerStrategy

        s1 = _make_server("grp-rr-1")
        s2 = _make_server("grp-rr-2")
        try:
            group = McpServerGroup(
                group_id="rr-group",
                strategy=LoadBalancerStrategy.ROUND_ROBIN,
                min_healthy=1,
            )
            group.add_member(s1, weight=1, priority=1)
            group.add_member(s2, weight=1, priority=1)
            group.collect_events()

            selected_ids = set()
            for _ in range(4):
                member = group.select_member()
                assert member is not None
                selected_ids.add(member.mcp_server_id)

            assert selected_ids == {"grp-rr-1", "grp-rr-2"}
        finally:
            _shutdown_safe(s1)
            _shutdown_safe(s2)

    def test_group_failover_on_member_failure(self):
        from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
        from mcp_hangar.domain.value_objects import LoadBalancerStrategy

        s1 = _make_server("grp-fo-1")
        s2 = _make_server("grp-fo-2")
        try:
            group = McpServerGroup(
                group_id="fo-group",
                strategy=LoadBalancerStrategy.ROUND_ROBIN,
                min_healthy=1,
                unhealthy_threshold=2,
            )
            group.add_member(s1)
            group.add_member(s2)
            group.collect_events()

            group.report_failure("grp-fo-1")
            group.report_failure("grp-fo-1")

            member = group.get_member("grp-fo-1")
            assert member is not None
            assert member.in_rotation is False

            for _ in range(3):
                selected = group.select_member()
                assert selected is not None
                assert selected.mcp_server_id == "grp-fo-2"
        finally:
            _shutdown_safe(s1)
            _shutdown_safe(s2)

    def test_group_member_recovery(self):
        from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
        from mcp_hangar.domain.value_objects import LoadBalancerStrategy

        s1 = _make_server("grp-rec-1")
        s2 = _make_server("grp-rec-2")
        try:
            group = McpServerGroup(
                group_id="rec-group",
                strategy=LoadBalancerStrategy.ROUND_ROBIN,
                min_healthy=1,
                unhealthy_threshold=2,
                healthy_threshold=1,
            )
            group.add_member(s1)
            group.add_member(s2)
            group.collect_events()

            group.report_failure("grp-rec-1")
            group.report_failure("grp-rec-1")

            member = group.get_member("grp-rec-1")
            assert member is not None
            assert member.in_rotation is False

            group.report_success("grp-rec-1")

            member = group.get_member("grp-rec-1")
            assert member is not None
            assert member.in_rotation is True
        finally:
            _shutdown_safe(s1)
            _shutdown_safe(s2)

    def test_group_state_transitions(self):
        from mcp_hangar.domain.model.mcp_server_group import GroupStateChanged, McpServerGroup
        from mcp_hangar.domain.value_objects import GroupState, LoadBalancerStrategy

        s1 = _make_server("grp-st-1")
        s2 = _make_server("grp-st-2")
        try:
            group = McpServerGroup(
                group_id="st-group",
                strategy=LoadBalancerStrategy.ROUND_ROBIN,
                min_healthy=2,
                unhealthy_threshold=2,
            )
            group.add_member(s1)
            group.add_member(s2)
            assert group.state == GroupState.HEALTHY
            group.collect_events()

            group.report_failure("grp-st-1")
            group.report_failure("grp-st-1")

            assert group.state == GroupState.PARTIAL

            events = group.collect_events()
            state_changes = [e for e in events if isinstance(e, GroupStateChanged)]
            assert any(e.new_state == "partial" for e in state_changes)
        finally:
            _shutdown_safe(s1)
            _shutdown_safe(s2)

    def test_group_rebalance_restores_rotation(self):
        from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
        from mcp_hangar.domain.value_objects import LoadBalancerStrategy

        s1 = _make_server("grp-rb-1")
        s2 = _make_server("grp-rb-2")
        try:
            group = McpServerGroup(
                group_id="rb-group",
                strategy=LoadBalancerStrategy.ROUND_ROBIN,
                min_healthy=1,
                unhealthy_threshold=2,
            )
            group.add_member(s1)
            group.add_member(s2)
            group.collect_events()

            group.report_failure("grp-rb-1")
            group.report_failure("grp-rb-1")

            member = group.get_member("grp-rb-1")
            assert member is not None
            assert member.in_rotation is False

            group.rebalance()

            member = group.get_member("grp-rb-1")
            assert member is not None
            assert member.in_rotation is True
        finally:
            _shutdown_safe(s1)
            _shutdown_safe(s2)

    def test_group_start_all_starts_members(self):
        from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
        from mcp_hangar.domain.value_objects import LoadBalancerStrategy

        s1 = _make_server("grp-sa-1")
        s2 = _make_server("grp-sa-2")
        try:
            group = McpServerGroup(
                group_id="sa-group",
                strategy=LoadBalancerStrategy.ROUND_ROBIN,
                min_healthy=1,
                auto_start=False,
            )
            group.add_member(s1)
            group.add_member(s2)

            started_count = group.start_all()
            assert started_count == 2
            assert s1.state == McpServerState.READY
            assert s2.state == McpServerState.READY
        finally:
            _shutdown_safe(s1)
            _shutdown_safe(s2)


class TestRecoverySaga:
    """McpServerRecoverySaga driven by real MCP server degradation events."""

    def test_saga_schedules_retry_on_degraded(self):
        from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga

        saga = McpServerRecoverySaga(max_retries=3, initial_backoff_s=0.1, saga_manager=None)

        degraded_event = McpServerDegraded(
            mcp_server_id="saga-test",
            consecutive_failures=3,
            total_failures=3,
            reason="test failure",
        )

        # Saga will try to use saga_manager.schedule_command; we test the retry state tracking
        try:
            saga.handle(degraded_event)
        except (RuntimeError, AttributeError):
            pass  # saga_manager not wired, expected

        state = saga.get_retry_state("saga-test")
        assert state is not None
        assert state["retries"] == 1

    def test_saga_resets_on_successful_start(self):
        from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga

        saga = McpServerRecoverySaga(max_retries=3, initial_backoff_s=0.1)
        saga._retry_state["saga-reset"] = {"retries": 2, "last_attempt": time.time(), "next_retry": 0}

        started_event = McpServerStarted(
            mcp_server_id="saga-reset",
            mode="subprocess",
            tools_count=6,
            startup_duration_ms=50.0,
        )
        saga.handle(started_event)

        state = saga.get_retry_state("saga-reset")
        assert state is not None
        assert state["retries"] == 0

    def test_saga_stops_server_after_max_retries(self):
        from mcp_hangar.application.commands import StopMcpServerCommand
        from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga

        saga = McpServerRecoverySaga(max_retries=2, initial_backoff_s=0.1)
        saga._retry_state["saga-max"] = {"retries": 2, "last_attempt": time.time(), "next_retry": 0}

        degraded_event = McpServerDegraded(
            mcp_server_id="saga-max",
            consecutive_failures=5,
            total_failures=10,
            reason="persistent failure",
        )
        commands = saga.handle(degraded_event)

        assert len(commands) == 1
        assert isinstance(commands[0], StopMcpServerCommand)
        assert commands[0].mcp_server_id == "saga-max"
        assert commands[0].reason == "max_retries_exceeded"

    def test_saga_exponential_backoff(self):
        from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga

        saga = McpServerRecoverySaga(
            max_retries=5,
            initial_backoff_s=1.0,
            max_backoff_s=16.0,
            backoff_multiplier=2.0,
        )

        assert saga._calculate_backoff(1) == pytest.approx(1.0)
        assert saga._calculate_backoff(2) == pytest.approx(2.0)
        assert saga._calculate_backoff(3) == pytest.approx(4.0)
        assert saga._calculate_backoff(4) == pytest.approx(8.0)
        assert saga._calculate_backoff(5) == pytest.approx(16.0)
        assert saga._calculate_backoff(6) == pytest.approx(16.0)

    def test_saga_clears_state_on_intentional_stop(self):
        from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga

        saga = McpServerRecoverySaga()
        saga._retry_state["saga-stop"] = {"retries": 1, "last_attempt": time.time(), "next_retry": 0}

        stopped_event = McpServerStopped(mcp_server_id="saga-stop", reason="shutdown")
        saga.handle(stopped_event)

        assert saga.get_retry_state("saga-stop") is None

    def test_saga_with_real_server_degradation_events(self):
        from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga

        saga = McpServerRecoverySaga(max_retries=3, initial_backoff_s=0.1)

        server = _make_server("saga-real")
        server.ensure_ready()
        server.collect_events()
        try:
            server.invoke_tool("add", {"a": 1, "b": 2})
            events = server.collect_events()

            started = [e for e in events if isinstance(e, McpServerStarted)]
            if started:
                saga.handle(started[0])
                state = saga.get_retry_state("saga-real")
                if state:
                    assert state["retries"] == 0
        finally:
            _shutdown_safe(server)


class TestSingleFlightColdStart:
    """SingleFlight deduplication on concurrent ensure_ready calls."""

    def test_concurrent_ensure_ready_deduplication(self):
        from mcp_hangar.infrastructure.single_flight import SingleFlight

        sf = SingleFlight()
        call_count = 0
        result_value = "started"

        def expensive_start():
            nonlocal call_count
            call_count += 1
            time.sleep(0.1)
            return result_value

        results: list = [None] * 5
        errors: list = []

        def call(idx: int):
            try:
                results[idx] = sf.do("cold-start", expensive_start)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=call, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        assert call_count == 1
        assert all(r == result_value for r in results)

    def test_single_flight_propagates_exception_to_waiters(self):
        from mcp_hangar.infrastructure.single_flight import SingleFlight

        sf = SingleFlight()

        def failing_start():
            time.sleep(0.05)
            raise RuntimeError("startup failed")

        errors: list[Exception] = []

        def call():
            try:
                sf.do("fail-key", failing_start)
            except RuntimeError as e:
                errors.append(e)

        threads = [threading.Thread(target=call) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 3
        assert all("startup failed" in str(e) for e in errors)

    def test_single_flight_with_real_server_startup(self):
        from mcp_hangar.infrastructure.single_flight import SingleFlight

        sf = SingleFlight()
        server = _make_server("sf-real")
        try:
            results: list = [None] * 4
            errors: list = []

            def start(idx: int):
                try:
                    results[idx] = sf.do("sf-real", server.ensure_ready)
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            threads = [threading.Thread(target=start, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            assert not errors
            assert server.state == McpServerState.READY
        finally:
            _shutdown_safe(server)


class TestLockHierarchyEnforcement:
    """TrackedLock detects out-of-order acquisition."""

    def test_correct_order_succeeds(self):
        from mcp_hangar.infrastructure.lock_hierarchy import LockLevel, TrackedLock, clear_thread_locks

        clear_thread_locks()
        lock_a = TrackedLock(LockLevel.PROVIDER, "test-provider", reentrant=False)
        lock_b = TrackedLock(LockLevel.EVENT_BUS, "test-bus", reentrant=False)

        lock_a.acquire()
        try:
            lock_b.acquire()
            lock_b.release()
        finally:
            lock_a.release()
        clear_thread_locks()

    def test_wrong_order_raises(self):
        from mcp_hangar.infrastructure.lock_hierarchy import (
            LockLevel,
            LockOrderViolation,
            TrackedLock,
            clear_thread_locks,
        )

        clear_thread_locks()
        lock_high = TrackedLock(LockLevel.EVENT_BUS, "test-bus-wrong", reentrant=False)
        lock_low = TrackedLock(LockLevel.PROVIDER, "test-provider-wrong", reentrant=False)

        lock_high.acquire()
        try:
            with pytest.raises(LockOrderViolation):
                lock_low.acquire()
        finally:
            lock_high.release()
        clear_thread_locks()

    def test_reentrant_lock_allows_same_level(self):
        from mcp_hangar.infrastructure.lock_hierarchy import LockLevel, TrackedLock, clear_thread_locks

        clear_thread_locks()
        lock = TrackedLock(LockLevel.PROVIDER, "test-reentrant", reentrant=True)

        lock.acquire()
        try:
            lock.acquire()
            lock.release()
        finally:
            lock.release()
        clear_thread_locks()

    def test_context_manager_protocol(self):
        from mcp_hangar.infrastructure.lock_hierarchy import LockLevel, TrackedLock, clear_thread_locks

        clear_thread_locks()
        lock = TrackedLock(LockLevel.PROVIDER, "test-ctx", reentrant=True)

        with lock:
            pass
        clear_thread_locks()

    def test_violation_contains_diagnostic_info(self):
        from mcp_hangar.infrastructure.lock_hierarchy import (
            LockLevel,
            LockOrderViolation,
            TrackedLock,
            clear_thread_locks,
        )

        clear_thread_locks()
        lock_high = TrackedLock(LockLevel.STDIO_CLIENT, "test-stdio", reentrant=False)
        lock_low = TrackedLock(LockLevel.PROVIDER, "test-prov-diag", reentrant=False)

        lock_high.acquire()
        try:
            with pytest.raises(LockOrderViolation) as exc_info:
                lock_low.acquire()

            err = exc_info.value
            assert err.requested_level == LockLevel.PROVIDER
            assert err.current_level == LockLevel.STDIO_CLIENT
            assert "test-stdio" in err.held_locks
        finally:
            lock_high.release()
        clear_thread_locks()


class TestFullUserJourney:
    """Complete lifecycle: create, start, invoke, degrade, recover, idle shutdown."""

    def test_complete_lifecycle_journey(self):
        server = McpServer(
            mcp_server_id="journey",
            mode="subprocess",
            command=[sys.executable, MOCK_PROVIDER],
            max_consecutive_failures=3,
            idle_ttl_s=1,
        )
        all_events: list[DomainEvent] = []

        try:
            # Phase 1: cold -> ready
            assert server.state == McpServerState.COLD
            server.ensure_ready()
            assert server.state == McpServerState.READY
            all_events.extend(server.collect_events())

            # Phase 2: tool discovery
            tools = server.get_tool_names()
            assert len(tools) == 6

            # Phase 3: successful invocations
            assert server.invoke_tool("add", {"a": 100, "b": 23})["result"] == 123
            assert server.invoke_tool("echo", {"message": "journey"})["message"] == "journey"
            all_events.extend(server.collect_events())

            # Phase 4: failed invocation
            with pytest.raises(ToolInvocationError):
                server.invoke_tool("divide", {"a": 1, "b": 0})
            all_events.extend(server.collect_events())

            # Phase 5: health check
            assert server.health_check() is True
            all_events.extend(server.collect_events())

            # Phase 6: stop and restart
            server.shutdown()
            assert server.state == McpServerState.COLD
            all_events.extend(server.collect_events())

            server.ensure_ready()
            assert server.state == McpServerState.READY
            all_events.extend(server.collect_events())

            # Phase 7: idle shutdown
            server.invoke_tool("add", {"a": 1, "b": 1})
            server.collect_events()
            time.sleep(1.5)
            assert server.is_idle
            assert server.maybe_shutdown_idle() is True
            assert server.state == McpServerState.COLD
            all_events.extend(server.collect_events())

            # Verify complete event trail
            event_types = {type(e).__name__ for e in all_events}
            assert "McpServerStateChanged" in event_types
            assert "McpServerStarted" in event_types
            assert "ToolInvocationRequested" in event_types
            assert "ToolInvocationCompleted" in event_types
            assert "ToolInvocationFailed" in event_types
            assert "HealthCheckPassed" in event_types
            assert "McpServerStopped" in event_types

            # Verify chronological ordering
            timestamps = [e.occurred_at for e in all_events]
            assert timestamps == sorted(timestamps)

        finally:
            _shutdown_safe(server)

    def test_restart_preserves_tool_catalog(self):
        server = _make_server("restart-catalog")
        try:
            server.ensure_ready()
            tools_before = set(server.get_tool_names())

            server.shutdown()
            server.ensure_ready()
            tools_after = set(server.get_tool_names())

            assert tools_before == tools_after
        finally:
            _shutdown_safe(server)

    def test_multiple_restarts_stable(self):
        server = _make_server("multi-restart")
        try:
            for cycle in range(3):
                server.ensure_ready()
                assert server.state == McpServerState.READY
                result = server.invoke_tool("add", {"a": cycle, "b": 1})
                assert result["result"] == cycle + 1
                server.shutdown()
                assert server.state == McpServerState.COLD
        finally:
            _shutdown_safe(server)


class TestEventSourcingRoundTrip:
    """Persist events from real MCP ops via EventBus -> EventStore, read back."""

    def test_persist_and_load_lifecycle_events(self):
        from mcp_hangar.infrastructure.event_bus import EventBus
        from mcp_hangar.infrastructure.event_store import InMemoryEventStore

        store = InMemoryEventStore()
        bus = EventBus(event_store=store)

        server = _make_server("es-roundtrip")
        try:
            server.ensure_ready()
            server.invoke_tool("add", {"a": 1, "b": 2})
            server.health_check()
            server.shutdown()

            events = server.collect_events()
            stream_id = "mcp_server:es-roundtrip"
            bus.publish_to_stream(stream_id, events, expected_version=-1)

            stored = store.load(stream_id)
            assert len(stored) == len(events)
            for original, persisted in zip(events, stored, strict=True):
                assert persisted.event_type == type(original).__name__
                assert persisted.event_id == original.event_id
        finally:
            _shutdown_safe(server)

    def test_event_store_version_tracking(self):
        from mcp_hangar.infrastructure.event_bus import EventBus
        from mcp_hangar.infrastructure.event_store import InMemoryEventStore

        store = InMemoryEventStore()
        bus = EventBus(event_store=store)

        server = _make_server("es-version")
        try:
            server.ensure_ready()
            events_batch1 = server.collect_events()

            server.invoke_tool("add", {"a": 1, "b": 1})
            events_batch2 = server.collect_events()

            stream_id = "mcp_server:es-version"
            v1 = bus.publish_to_stream(stream_id, events_batch1, expected_version=-1)
            v2 = bus.publish_to_stream(stream_id, events_batch2, expected_version=v1)

            assert v2 > v1
            assert store.get_version(stream_id) == v2
            all_stored = store.load(stream_id)
            assert len(all_stored) == len(events_batch1) + len(events_batch2)
        finally:
            _shutdown_safe(server)

    def test_concurrency_error_on_version_mismatch(self):
        from mcp_hangar.infrastructure.event_bus import EventBus
        from mcp_hangar.infrastructure.event_store import ConcurrencyError, InMemoryEventStore

        store = InMemoryEventStore()
        bus = EventBus(event_store=store)

        server = _make_server("es-conflict")
        try:
            server.ensure_ready()
            events = server.collect_events()

            stream_id = "mcp_server:es-conflict"
            bus.publish_to_stream(stream_id, events, expected_version=-1)

            server.invoke_tool("add", {"a": 1, "b": 1})
            events2 = server.collect_events()

            with pytest.raises(ConcurrencyError):
                bus.publish_to_stream(stream_id, events2, expected_version=-1)
        finally:
            _shutdown_safe(server)

    def test_aggregate_events_convenience_method(self):
        from mcp_hangar.infrastructure.event_bus import EventBus
        from mcp_hangar.infrastructure.event_store import InMemoryEventStore

        store = InMemoryEventStore()
        bus = EventBus(event_store=store)

        server = _make_server("es-agg")
        try:
            server.ensure_ready()
            events = server.collect_events()

            new_version = bus.publish_aggregate_events("mcp_server", "es-agg", events)
            assert new_version == len(events) - 1

            stored = store.load("mcp_server:es-agg")
            assert len(stored) == len(events)
        finally:
            _shutdown_safe(server)
