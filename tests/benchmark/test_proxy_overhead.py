"""Performance benchmarks for the proxy hot path.

Measures the overhead added by mcp-hangar's processing layers, excluding
the actual downstream RPC to the MCP server. The RPC is replaced with a
mock that returns instantly, isolating pure proxy overhead.

Benchmark categories:
1. Provider.invoke_tool() -- full aggregate overhead (lock cycles, events, health)
2. ToolAccessResolver -- policy evaluation per call
3. EventBus.publish() -- event dispatch to handlers
4. CommandBus.send() -- CQRS dispatch pipeline
5. Identity context -- contextvar read overhead
6. Full proxy path -- InvokeToolHandler end-to-end (repo lookup + invoke + events)

Target: <5ms p99 for the full proxy path (excluding downstream RPC).
"""

import time
from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.commands.commands import InvokeToolCommand
from mcp_hangar.application.commands.handlers import InvokeToolHandler
from mcp_hangar.application.event_handlers.audit_handler import (
    AuditEventHandler,
    InMemoryAuditStore,
)
from mcp_hangar.application.event_handlers.metrics_handler import MetricsEventHandler
from mcp_hangar.context import get_identity_context, identity_context_var
from mcp_hangar.domain.events import (
    DomainEvent,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from mcp_hangar.domain.model import McpServer, McpServerState
from mcp_hangar.domain.model.health_tracker import HealthTracker
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.services.tool_access_resolver import (
    ToolAccessResolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ready_provider(
    mcp_server_id: str = "bench-provider",
    tool_names: list[str] | None = None,
) -> tuple[McpServer, MagicMock]:
    """Create a READY provider with pre-loaded tools and a zero-cost mock client."""
    provider = McpServer(mcp_server_id=mcp_server_id, mode="subprocess",
    command=["python", "-m", "test"],)
    mock_client = MagicMock()
    mock_client.is_alive.return_value = True
    mock_client.call.return_value = {
        "result": {"content": [{"type": "text", "text": "ok"}]}
    }

    with provider._lock:
        provider._state = McpServerState.READY
        provider._client = mock_client

    for name in tool_names or ["calculator"]:
        provider._tools.add(
            ToolSchema(name=name, description=f"Tool {name}", input_schema={})
        )

    return provider, mock_client


def _make_event_bus_with_handlers() -> EventBus:
    """Create an event bus wired with production-like handlers."""
    bus = EventBus()
    metrics_handler = MetricsEventHandler()
    audit_handler = AuditEventHandler(store=InMemoryAuditStore())

    for event_type in (
        ToolInvocationRequested,
        ToolInvocationCompleted,
        ToolInvocationFailed,
    ):
        bus.subscribe(event_type, metrics_handler.handle)
        bus.subscribe(event_type, audit_handler.handle)

    return bus


# ===========================================================================
# 1. Provider.invoke_tool() -- core aggregate overhead
# ===========================================================================


@pytest.mark.benchmark(group="proxy-overhead")
class TestProviderInvokeTool:
    """Benchmark Provider.invoke_tool() with a zero-cost mock client.

    This measures the pure aggregate overhead: lock acquisition, tool catalog
    lookup, identity context read, domain event recording, health tracker
    update. The mock client returns instantly, so all measured time is proxy
    overhead.
    """

    def test_invoke_tool_single_call(self, benchmark):
        """Single tool invocation overhead."""
        provider, mock_client = _make_ready_provider()

        def invoke():
            result = provider.invoke_tool("calculator", {"a": 1, "b": 2})
            # Drain recorded events so they don't accumulate
            provider.collect_events()
            return result

        result = benchmark(invoke)
        assert result == {"content": [{"type": "text", "text": "ok"}]}

    def test_invoke_tool_with_identity_context(self, benchmark):
        """Invocation overhead when identity context is set."""
        provider, _ = _make_ready_provider()
        idt = IdentityContext(
            caller=CallerIdentity(
                user_id="user-123",
                agent_id="agent-456",
                session_id="sess-789",
                principal_type="service",
            )
        )

        def invoke():
            token = identity_context_var.set(idt)
            try:
                result = provider.invoke_tool("calculator", {"a": 1, "b": 2})
                provider.collect_events()
                return result
            finally:
                identity_context_var.reset(token)

        result = benchmark(invoke)
        assert result == {"content": [{"type": "text", "text": "ok"}]}

    def test_invoke_tool_10_tools_catalog(self, benchmark):
        """Invocation with a larger tool catalog (10 tools)."""
        tools = [f"tool_{i}" for i in range(10)]
        provider, _ = _make_ready_provider(tool_names=tools)

        def invoke():
            result = provider.invoke_tool("tool_5", {"x": 42})
            provider.collect_events()
            return result

        benchmark(invoke)

    def test_invoke_tool_100_tools_catalog(self, benchmark):
        """Invocation with a large tool catalog (100 tools)."""
        tools = [f"tool_{i}" for i in range(100)]
        provider, _ = _make_ready_provider(tool_names=tools)

        def invoke():
            result = provider.invoke_tool("tool_50", {"x": 42})
            provider.collect_events()
            return result

        benchmark(invoke)


# ===========================================================================
# 2. ToolAccessResolver -- policy evaluation
# ===========================================================================


@pytest.mark.benchmark(group="policy-evaluation")
class TestToolAccessResolver:
    """Benchmark the policy resolution hot path."""

    def setup_method(self):
        reset_tool_access_resolver()

    def teardown_method(self):
        reset_tool_access_resolver()

    def test_is_tool_allowed_no_policy(self, benchmark):
        """Check with no policies configured (default allow)."""
        resolver = ToolAccessResolver()

        def check():
            return resolver.is_tool_allowed("provider-a", "read_file")

        result = benchmark(check)
        assert result is True

    def test_is_tool_allowed_with_deny_list(self, benchmark):
        """Check against a provider with deny list."""
        resolver = ToolAccessResolver()
        resolver.set_mcp_server_policy(
            "provider-a",
            ToolAccessPolicy(deny_list=("delete_*", "drop_*", "truncate_*")),
        )

        def check():
            return resolver.is_tool_allowed("provider-a", "read_file")

        result = benchmark(check)
        assert result is True

    def test_is_tool_allowed_with_allow_list(self, benchmark):
        """Check against a provider with explicit allow list."""
        resolver = ToolAccessResolver()
        resolver.set_mcp_server_policy(
            "provider-a",
            ToolAccessPolicy(allow_list=("read_*", "list_*", "get_*")),
        )

        def check():
            return resolver.is_tool_allowed("provider-a", "read_file")

        result = benchmark(check)
        assert result is True

    def test_is_tool_allowed_three_level_merge(self, benchmark):
        """Full three-level policy merge (provider + group + member)."""
        resolver = ToolAccessResolver()
        resolver.set_mcp_server_policy(
            "provider-a",
            ToolAccessPolicy(deny_list=("admin_*",)),
        )
        resolver.set_group_policy(
            "group-1",
            ToolAccessPolicy(deny_list=("debug_*",)),
        )
        resolver.set_member_policy(
            "group-1",
            "member-1",
            ToolAccessPolicy(allow_list=("read_*", "list_*")),
        )

        def check():
            return resolver.is_tool_allowed(
                "provider-a", "read_file", group_id="group-1", member_id="member-1"
            )

        result = benchmark(check)
        assert result is True


# ===========================================================================
# 3. EventBus.publish() -- event dispatch overhead
# ===========================================================================


@pytest.mark.benchmark(group="event-dispatch")
class TestEventBusPublish:
    """Benchmark event bus dispatch with production-like handler counts."""

    def test_publish_no_handlers(self, benchmark):
        """Publish with zero handlers (baseline)."""
        bus = EventBus()
        event = ToolInvocationCompleted(mcp_server_id="bench", tool_name="calculator",
        correlation_id="corr-1",
        duration_ms=1.5,
        result_size_bytes=64,)

        benchmark(bus.publish, event)

    def test_publish_with_metrics_and_audit(self, benchmark):
        """Publish with metrics + audit handlers (production-like)."""
        bus = _make_event_bus_with_handlers()
        event = ToolInvocationCompleted(mcp_server_id="bench", tool_name="calculator",
        correlation_id="corr-1",
        duration_ms=1.5,
        result_size_bytes=64,)

        benchmark(bus.publish, event)

    def test_publish_tool_invocation_requested(self, benchmark):
        """Publish ToolInvocationRequested (emitted before RPC)."""
        bus = _make_event_bus_with_handlers()
        event = ToolInvocationRequested(mcp_server_id="bench", tool_name="calculator",
        correlation_id="corr-1",
        arguments={"a": 1, "b": 2},)

        benchmark(bus.publish, event)

    def test_publish_5_handlers(self, benchmark):
        """Publish with 5 handlers subscribed (stress test)."""
        bus = EventBus()
        counters = [0] * 5

        for i in range(5):
            idx = i

            def handler(event, _idx=idx):
                counters[_idx] += 1

            bus.subscribe(ToolInvocationCompleted, handler)

        event = ToolInvocationCompleted(mcp_server_id="bench", tool_name="calculator",
        correlation_id="corr-1",
        duration_ms=1.5,
        result_size_bytes=64,)

        benchmark(bus.publish, event)
        assert all(c > 0 for c in counters)


# ===========================================================================
# 4. CommandBus.send() -- CQRS dispatch pipeline
# ===========================================================================


@pytest.mark.benchmark(group="command-bus")
class TestCommandBusSend:
    """Benchmark the command bus dispatch chain."""

    def test_send_invoke_tool_command(self, benchmark):
        """Full command bus send for InvokeToolCommand."""
        provider, _ = _make_ready_provider()
        repository = InMemoryMcpServerRepository()
        repository.add("bench-provider", provider)
        event_bus = _make_event_bus_with_handlers()

        command_bus = CommandBus()
        handler = InvokeToolHandler(repository, event_bus)
        command_bus.register(InvokeToolCommand, handler)

        cmd = InvokeToolCommand(mcp_server_id="bench-provider", tool_name="calculator",
        arguments={"a": 1, "b": 2},)

        def send():
            return command_bus.send(cmd)

        result = benchmark(send)
        assert "content" in result

    def test_send_without_middleware(self, benchmark):
        """Command bus dispatch without middleware (raw handler)."""
        provider, _ = _make_ready_provider()
        repository = InMemoryMcpServerRepository()
        repository.add("bench-provider", provider)
        event_bus = EventBus()  # no handlers for minimal overhead

        command_bus = CommandBus()
        handler = InvokeToolHandler(repository, event_bus)
        command_bus.register(InvokeToolCommand, handler)

        cmd = InvokeToolCommand(mcp_server_id="bench-provider", tool_name="calculator",
        arguments={"a": 1, "b": 2},)

        def send():
            return command_bus.send(cmd)

        benchmark(send)


# ===========================================================================
# 5. Identity context read -- contextvar overhead
# ===========================================================================


@pytest.mark.benchmark(group="identity")
class TestIdentityContext:
    """Benchmark identity context operations on the hot path."""

    def test_get_identity_context_none(self, benchmark):
        """Read identity context when not set (most common path)."""
        benchmark(get_identity_context)

    def test_get_identity_context_set(self, benchmark):
        """Read identity context when set."""
        idt = IdentityContext(
            caller=CallerIdentity(
                user_id="user-123",
                agent_id="agent-456",
                session_id="sess-789",
                principal_type="service",
            )
        )
        token = identity_context_var.set(idt)

        try:
            result = benchmark(get_identity_context)
            assert result is not None
            assert result.caller.user_id == "user-123"
        finally:
            identity_context_var.reset(token)

    def test_identity_context_to_dict(self, benchmark):
        """Convert identity context to dict (done per event recording)."""
        idt = IdentityContext(
            caller=CallerIdentity(
                user_id="user-123",
                agent_id="agent-456",
                session_id="sess-789",
                principal_type="service",
            )
        )

        benchmark(idt.to_dict)


# ===========================================================================
# 6. HealthTracker -- degradation check
# ===========================================================================


@pytest.mark.benchmark(group="health")
class TestHealthTracker:
    """Benchmark health tracker checks on the hot path."""

    def test_should_degrade_healthy(self, benchmark):
        """Check degradation on a healthy provider."""
        tracker = HealthTracker()

        benchmark(tracker.should_degrade)

    def test_record_success(self, benchmark):
        """Record a successful invocation."""
        tracker = HealthTracker()

        benchmark(tracker.record_success)


# ===========================================================================
# 7. Full proxy path -- end-to-end overhead measurement
# ===========================================================================


@pytest.mark.benchmark(group="full-proxy-path")
class TestFullProxyPath:
    """Benchmark the complete proxy overhead: command bus + invoke + events.

    This is the primary benchmark for the <5ms p99 target. It measures
    everything that happens between receiving the tool call request and
    returning the result, excluding the actual downstream RPC.
    """

    def _setup_full_path(self):
        """Set up the full command dispatch infrastructure."""
        provider, mock_client = _make_ready_provider(
            tool_names=["calculator", "search", "fetch", "parse", "validate"]
        )
        repository = InMemoryMcpServerRepository()
        repository.add("bench-provider", provider)
        event_bus = _make_event_bus_with_handlers()

        command_bus = CommandBus()
        handler = InvokeToolHandler(repository, event_bus)
        command_bus.register(InvokeToolCommand, handler)

        return command_bus, provider

    def test_full_path_single_tool(self, benchmark):
        """Full proxy path: command bus -> handler -> invoke -> events."""
        command_bus, provider = self._setup_full_path()
        cmd = InvokeToolCommand(mcp_server_id="bench-provider", tool_name="calculator",
        arguments={"a": 1, "b": 2},)

        result = benchmark(command_bus.send, cmd)
        assert "content" in result

    def test_full_path_with_identity(self, benchmark):
        """Full proxy path with identity context set."""
        command_bus, provider = self._setup_full_path()
        cmd = InvokeToolCommand(mcp_server_id="bench-provider", tool_name="calculator",
        arguments={"a": 1, "b": 2},)
        idt = IdentityContext(
            caller=CallerIdentity(
                user_id="user-123",
                agent_id="agent-456",
                session_id="sess-789",
                principal_type="service",
            )
        )

        def invoke():
            token = identity_context_var.set(idt)
            try:
                return command_bus.send(cmd)
            finally:
                identity_context_var.reset(token)

        result = benchmark(invoke)
        assert "content" in result

    def test_full_path_p99_assertion(self, benchmark):
        """Full proxy path with explicit p99 < 5ms assertion.

        This is the key benchmark for task 11.1. If this fails, the proxy
        overhead exceeds the target and optimization is needed.
        """
        command_bus, provider = self._setup_full_path()
        cmd = InvokeToolCommand(mcp_server_id="bench-provider", tool_name="calculator",
        arguments={"a": 1, "b": 2},)

        # Run benchmark and collect raw timing data
        timings = []

        def invoke():
            start = time.perf_counter_ns()
            result = command_bus.send(cmd)
            elapsed_ns = time.perf_counter_ns() - start
            timings.append(elapsed_ns)
            return result

        benchmark(invoke)

        # Verify p99 < 5ms
        if len(timings) >= 10:
            timings.sort()
            p99_index = int(len(timings) * 0.99)
            p99_ns = timings[p99_index]
            p99_ms = p99_ns / 1_000_000
            p50_ns = timings[len(timings) // 2]
            p50_ms = p50_ns / 1_000_000

            # Informational output (visible with -s flag)
            print(f"\n  Proxy overhead: p50={p50_ms:.3f}ms, p99={p99_ms:.3f}ms, n={len(timings)}")
            assert p99_ms < 5.0, f"p99 proxy overhead {p99_ms:.3f}ms exceeds 5ms target"
