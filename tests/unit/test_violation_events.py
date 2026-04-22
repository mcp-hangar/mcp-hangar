"""Unit tests for violation infrastructure: value objects, events, metrics, and handler wiring."""

from mcp_hangar.domain.value_objects.capabilities import ViolationType, ViolationSeverity
from mcp_hangar.domain.events import (
    CapabilityViolationDetected,
    EgressBlocked,
    McpServerCapabilityQuarantined,
    McpServerCapabilityQuarantineReleased,
    McpServerQuarantined,
)
from mcp_hangar.observability.conventions import Enforcement


# ============================================================================
# ViolationType enum
# ============================================================================


class TestViolationType:
    def test_has_egress_denied_member(self) -> None:
        assert ViolationType.EGRESS_DENIED.value == "egress_denied"

    def test_has_capability_drift_member(self) -> None:
        assert ViolationType.CAPABILITY_DRIFT.value == "capability_drift"

    def test_has_undeclared_tool_member(self) -> None:
        assert ViolationType.UNDECLARED_TOOL.value == "undeclared_tool"

    def test_has_schema_mismatch_member(self) -> None:
        assert ViolationType.SCHEMA_MISMATCH.value == "schema_mismatch"

    def test_has_quarantine_triggered_member(self) -> None:
        assert ViolationType.QUARANTINE_TRIGGERED.value == "quarantine_triggered"

    def test_str_returns_value(self) -> None:
        assert str(ViolationType.EGRESS_DENIED) == "egress_denied"

    def test_string_construction(self) -> None:
        assert ViolationType("egress_denied") == ViolationType.EGRESS_DENIED


# ============================================================================
# ViolationSeverity enum
# ============================================================================


class TestViolationSeverity:
    def test_has_critical_member(self) -> None:
        assert ViolationSeverity.CRITICAL.value == "critical"

    def test_has_high_member(self) -> None:
        assert ViolationSeverity.HIGH.value == "high"

    def test_has_medium_member(self) -> None:
        assert ViolationSeverity.MEDIUM.value == "medium"

    def test_has_low_member(self) -> None:
        assert ViolationSeverity.LOW.value == "low"

    def test_str_returns_value(self) -> None:
        assert str(ViolationSeverity.HIGH) == "high"


# ============================================================================
# ProviderCapabilityQuarantined (renamed from duplicate ProviderQuarantined)
# ============================================================================


class TestProviderCapabilityQuarantined:
    def test_is_importable_and_has_expected_fields(self) -> None:
        event = McpServerCapabilityQuarantined(
            mcp_server_id="rogue-provider",
            reason="3 egress violations in 60s",
            violation_count=3,
        )
        assert event.mcp_server_id == "rogue-provider"
        assert event.reason == "3 egress violations in 60s"
        assert event.violation_count == 3
        assert event.schema_version == 1

    def test_discovery_provider_quarantined_still_exists(self) -> None:
        """The original discovery-related ProviderQuarantined must stay intact."""
        event = McpServerQuarantined(
            mcp_server_name="my-provider",
            source_type="filesystem",
            reason="validation failed",
            validation_result="invalid config",
        )
        assert event.mcp_server_name == "my-provider"
        assert event.source_type == "filesystem"
        assert event.reason == "validation failed"
        assert event.validation_result == "invalid config"

    def test_discovery_and_capability_quarantined_are_distinct_classes(self) -> None:
        assert McpServerQuarantined is not McpServerCapabilityQuarantined


class TestProviderCapabilityQuarantineReleased:
    def test_is_importable_and_has_expected_fields(self) -> None:
        event = McpServerCapabilityQuarantineReleased(
            mcp_server_id="rogue-provider",
            released_by="ops@example.com",
        )
        assert event.mcp_server_id == "rogue-provider"
        assert event.released_by == "ops@example.com"
        assert event.schema_version == 1


# ============================================================================
# CapabilityViolationDetected severity field
# ============================================================================


class TestCapabilityViolationDetectedSeverity:
    def test_has_severity_field_with_default(self) -> None:
        event = CapabilityViolationDetected(
            mcp_server_id="math",
            violation_type="egress_undeclared",
            violation_detail="Blocked connection",
            enforcement_action="alert",
        )
        assert event.severity == "high"

    def test_severity_can_be_overridden(self) -> None:
        event = CapabilityViolationDetected(
            mcp_server_id="math",
            violation_type="egress_undeclared",
            violation_detail="Blocked connection",
            enforcement_action="alert",
            severity="critical",
        )
        assert event.severity == "critical"

    def test_schema_version_is_2(self) -> None:
        event = CapabilityViolationDetected(
            mcp_server_id="math",
            violation_type="egress_undeclared",
            violation_detail="Blocked",
            enforcement_action="alert",
        )
        assert event.schema_version == 2

    def test_all_expected_fields_present(self) -> None:
        event = CapabilityViolationDetected(
            mcp_server_id="math",
            violation_type="egress_undeclared",
            violation_detail="Connection to 192.168.1.100:9200",
            enforcement_action="alert",
            destination="192.168.1.100:9200",
        )
        assert event.mcp_server_id == "math"
        assert event.violation_type == "egress_undeclared"
        assert event.violation_detail == "Connection to 192.168.1.100:9200"
        assert event.enforcement_action == "alert"
        assert event.destination == "192.168.1.100:9200"
        assert event.event_id is not None
        assert event.occurred_at > 0


# ============================================================================
# Enforcement.VIOLATION_SEVERITY OTEL attribute
# ============================================================================


class TestEnforcementViolationSeverity:
    def test_violation_severity_attribute_defined(self) -> None:
        assert Enforcement.VIOLATION_SEVERITY == "mcp.enforcement.violation_severity"


# ============================================================================
# CAPABILITY_VIOLATIONS_TOTAL Prometheus counter
# ============================================================================


class TestCapabilityViolationsCounter:
    def test_counter_exists_in_metrics_module(self) -> None:
        from mcp_hangar.metrics import CAPABILITY_VIOLATIONS_TOTAL

        assert CAPABILITY_VIOLATIONS_TOTAL is not None
        assert CAPABILITY_VIOLATIONS_TOTAL.name == "mcp_hangar_capability_violations"

    def test_counter_has_correct_labels(self) -> None:
        from mcp_hangar.metrics import CAPABILITY_VIOLATIONS_TOTAL

        assert CAPABILITY_VIOLATIONS_TOTAL.label_names == ["mcp_server", "violation_type"]

    def test_record_capability_violation_increments_counter(self) -> None:
        from mcp_hangar.metrics import CAPABILITY_VIOLATIONS_TOTAL, record_capability_violation

        # Clear any prior state
        CAPABILITY_VIOLATIONS_TOTAL._values.clear()
        CAPABILITY_VIOLATIONS_TOTAL._created.clear()

        record_capability_violation("test-provider", "egress_denied")

        samples = CAPABILITY_VIOLATIONS_TOTAL.collect()
        assert len(samples) == 1
        assert samples[0].value == 1.0
        assert samples[0].labels == {"mcp_server": "test-provider", "violation_type": "egress_denied"}

    def test_record_capability_violation_twice_increments_to_two(self) -> None:
        from mcp_hangar.metrics import CAPABILITY_VIOLATIONS_TOTAL, record_capability_violation

        # Clear any prior state
        CAPABILITY_VIOLATIONS_TOTAL._values.clear()
        CAPABILITY_VIOLATIONS_TOTAL._created.clear()

        record_capability_violation("test-provider", "egress_denied")
        record_capability_violation("test-provider", "egress_denied")

        samples = CAPABILITY_VIOLATIONS_TOTAL.collect()
        assert len(samples) == 1
        assert samples[0].value == 2.0


# ============================================================================
# MetricsEventHandler violation event routing
# ============================================================================


class TestMetricsEventHandlerViolationRouting:
    def test_handles_capability_violation_detected(self) -> None:
        from mcp_hangar.application.event_handlers.metrics_handler import MetricsEventHandler
        from mcp_hangar.metrics import CAPABILITY_VIOLATIONS_TOTAL

        # Clear any prior state
        CAPABILITY_VIOLATIONS_TOTAL._values.clear()
        CAPABILITY_VIOLATIONS_TOTAL._created.clear()

        handler = MetricsEventHandler()
        event = CapabilityViolationDetected(
            mcp_server_id="math",
            violation_type="capability_drift",
            violation_detail="Tool schema changed",
            enforcement_action="alert",
        )
        handler.handle(event)

        samples = CAPABILITY_VIOLATIONS_TOTAL.collect()
        assert len(samples) == 1
        assert samples[0].labels == {"mcp_server": "math", "violation_type": "capability_drift"}
        assert samples[0].value == 1.0

    def test_handles_egress_blocked(self) -> None:
        from mcp_hangar.application.event_handlers.metrics_handler import MetricsEventHandler
        from mcp_hangar.metrics import CAPABILITY_VIOLATIONS_TOTAL

        # Clear any prior state
        CAPABILITY_VIOLATIONS_TOTAL._values.clear()
        CAPABILITY_VIOLATIONS_TOTAL._created.clear()

        handler = MetricsEventHandler()
        event = EgressBlocked(
            mcp_server_id="rogue",
            destination_host="evil.com",
            destination_port=443,
            protocol="tcp",
        )
        handler.handle(event)

        samples = CAPABILITY_VIOLATIONS_TOTAL.collect()
        assert len(samples) == 1
        assert samples[0].labels == {"mcp_server": "rogue", "violation_type": "egress_denied"}
        assert samples[0].value == 1.0

    def test_existing_events_still_handled(self) -> None:
        """Ensure adding violation handling does not break existing event routing."""
        from mcp_hangar.application.event_handlers.metrics_handler import MetricsEventHandler
        from mcp_hangar.domain.events import McpServerStarted

        handler = MetricsEventHandler()
        event = McpServerStarted(
            mcp_server_id="math",
            mode="subprocess",
            tools_count=5,
            startup_duration_ms=100,
        )
        # Should not raise
        handler.handle(event)

        # Verify in-memory metrics were updated
        metrics = handler.get_metrics("math")
        assert metrics is not None
        assert metrics.mcp_server_id == "math"
