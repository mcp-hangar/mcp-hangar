"""Tests for runtime capability drift detection on the Provider aggregate.

Verifies _verify_capability_drift() behavior across enforcement modes,
and that McpServerRecoverySaga correctly filters capability_violation events.
"""


from mcp_hangar.domain.events import CapabilityViolationDetected, McpServerDegraded, McpServerStateChanged
from mcp_hangar.domain.model.provider import McpServer
from mcp_hangar.domain.value_objects import ProviderMode, ProviderState
from mcp_hangar.domain.value_objects.capabilities import (
    McpServerCapabilities,
    ToolCapabilities,
    ViolationSeverity,
    ViolationType,
)
from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga


def _make_provider(
    expected_tools: tuple[str, ...] = (),
    enforcement_mode: str = "alert",
) -> McpServer:
    """Create a Provider with capabilities and set it to READY state."""
    caps = McpServerCapabilities(
        tools=ToolCapabilities(expected_tools=expected_tools),
        enforcement_mode=enforcement_mode,
    )
    provider = McpServer(mcp_server_id="drift-test", mode=ProviderMode.SUBPROCESS,
    command=["echo"],
    capabilities=caps,)
    # Move to READY so drift check is meaningful
    provider._state = ProviderState.READY
    return provider


class TestVerifyCapabilityDrift:
    """Tests for Provider._verify_capability_drift()."""

    def test_no_capabilities_skips(self) -> None:
        """Provider without capabilities should skip drift check entirely."""
        provider = McpServer(mcp_server_id="no-caps", mode=ProviderMode.SUBPROCESS,
        command=["echo"],)
        provider._state = ProviderState.READY
        provider._tools.update_from_list(
            [
                {"name": "tool_a", "description": "A", "inputSchema": {}},
            ]
        )

        provider._verify_capability_drift()

        events = provider.collect_events()
        # No CapabilityViolationDetected events should be emitted
        violation_events = [e for e in events if isinstance(e, CapabilityViolationDetected)]
        assert violation_events == []

    def test_no_expected_tools_skips(self) -> None:
        """Provider with empty expected_tools should skip drift check."""
        provider = _make_provider(expected_tools=())
        provider._tools.update_from_list(
            [
                {"name": "tool_a", "description": "A", "inputSchema": {}},
            ]
        )

        provider._verify_capability_drift()

        events = provider.collect_events()
        violation_events = [e for e in events if isinstance(e, CapabilityViolationDetected)]
        assert violation_events == []

    def test_exact_match_no_violation(self) -> None:
        """Runtime tools matching expected_tools exactly should produce no violation."""
        provider = _make_provider(expected_tools=("tool_a", "tool_b"))
        provider._tools.update_from_list(
            [
                {"name": "tool_a", "description": "A", "inputSchema": {}},
                {"name": "tool_b", "description": "B", "inputSchema": {}},
            ]
        )

        provider._verify_capability_drift()

        events = provider.collect_events()
        violation_events = [e for e in events if isinstance(e, CapabilityViolationDetected)]
        assert violation_events == []

    def test_undeclared_tools_alert_mode_stays_ready(self) -> None:
        """In alert mode, undeclared tools emit event but provider stays READY."""
        provider = _make_provider(
            expected_tools=("tool_a",),
            enforcement_mode="alert",
        )
        provider._tools.update_from_list(
            [
                {"name": "tool_a", "description": "A", "inputSchema": {}},
                {"name": "tool_b", "description": "B", "inputSchema": {}},
            ]
        )

        provider._verify_capability_drift()

        # Provider stays READY in alert mode
        assert provider._state == ProviderState.READY

        events = provider.collect_events()
        violation_events = [e for e in events if isinstance(e, CapabilityViolationDetected)]
        assert len(violation_events) == 1

        v = violation_events[0]
        assert v.violation_type == ViolationType.SCHEMA_MISMATCH.value
        assert v.enforcement_action == "alert"
        assert v.severity == ViolationSeverity.HIGH.value

    def test_undeclared_tools_block_mode_goes_dead(self) -> None:
        """In block mode, undeclared tools transition provider to DEAD."""
        provider = _make_provider(
            expected_tools=("tool_a",),
            enforcement_mode="block",
        )
        provider._tools.update_from_list(
            [
                {"name": "tool_a", "description": "A", "inputSchema": {}},
                {"name": "tool_b", "description": "B", "inputSchema": {}},
            ]
        )

        provider._verify_capability_drift()

        # Provider transitions to DEAD in block mode
        assert provider._state == ProviderState.DEAD

        events = provider.collect_events()
        violation_events = [e for e in events if isinstance(e, CapabilityViolationDetected)]
        assert len(violation_events) == 1

        v = violation_events[0]
        assert v.violation_type == ViolationType.SCHEMA_MISMATCH.value
        assert v.enforcement_action == "block"

        # Should also have a McpServerStateChanged event for READY -> DEAD
        state_events = [e for e in events if isinstance(e, McpServerStateChanged)]
        assert any(e.new_state == ProviderState.DEAD.value for e in state_events)

    def test_missing_tools_not_flagged(self) -> None:
        """Tools in expected_tools but absent at runtime should NOT be violations."""
        provider = _make_provider(expected_tools=("tool_a", "tool_b", "tool_c"))
        provider._tools.update_from_list(
            [
                {"name": "tool_a", "description": "A", "inputSchema": {}},
            ]
        )

        provider._verify_capability_drift()

        events = provider.collect_events()
        violation_events = [e for e in events if isinstance(e, CapabilityViolationDetected)]
        assert violation_events == [], "missing expected tools should not produce violations"

    def test_violation_detail_contains_tool_names(self) -> None:
        """Violation detail should list the undeclared tool names."""
        provider = _make_provider(
            expected_tools=("tool_a",),
            enforcement_mode="alert",
        )
        provider._tools.update_from_list(
            [
                {"name": "tool_a", "description": "A", "inputSchema": {}},
                {"name": "secret_tool", "description": "S", "inputSchema": {}},
                {"name": "hidden_tool", "description": "H", "inputSchema": {}},
            ]
        )

        provider._verify_capability_drift()

        events = provider.collect_events()
        violation_events = [e for e in events if isinstance(e, CapabilityViolationDetected)]
        assert len(violation_events) == 1

        detail = violation_events[0].violation_detail
        assert "secret_tool" in detail
        assert "hidden_tool" in detail


class TestRecoverySagaCapabilityFilter:
    """Tests for McpServerRecoverySaga filtering of capability_violation events."""

    def test_saga_skips_capability_violation_reason(self) -> None:
        """Saga should return empty and NOT schedule for capability_violation: reason."""
        saga = McpServerRecoverySaga()

        event = McpServerDegraded(mcp_server_id="blocked-provider", consecutive_failures=1,
        total_failures=1,
        reason="capability_violation: undeclared tools detected",)

        commands = saga._handle_degraded(event)
        assert commands == [], "saga should skip auto-recovery for capability violations"
        # Verify no retry state was initialized (no scheduling happened)
        assert "blocked-provider" not in saga._retry_state

    def test_saga_proceeds_for_normal_degraded(self) -> None:
        """Saga should schedule recovery for normal degraded events.

        _handle_degraded() always returns [] but schedules a StartMcpServerCommand
        via saga_manager.schedule_command() for non-capability-violation events.
        We verify retry state is initialized (proving it entered the scheduling path).
        """
        saga = McpServerRecoverySaga()

        event = McpServerDegraded(mcp_server_id="normal-provider", consecutive_failures=1,
        total_failures=1,
        reason="health_check_failed",)

        commands = saga._handle_degraded(event)
        # Returns [] because command is scheduled asynchronously via saga_manager
        assert commands == []
        # But retry state IS initialized -- proves it entered the recovery path
        assert "normal-provider" in saga._retry_state
        assert saga._retry_state["normal-provider"]["retries"] == 1
