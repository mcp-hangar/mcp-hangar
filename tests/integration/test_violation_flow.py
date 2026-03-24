"""Integration tests for the violation event -> Prometheus + OTEL span flow.

Verifies that Plan 01's Python violation infrastructure works end-to-end:
domain events flow through MetricsEventHandler to Prometheus counters,
OTEL conventions are consistent, and event classes are correctly disambiguated.
"""

import pytest

from mcp_hangar.application.event_handlers.metrics_handler import MetricsEventHandler
from mcp_hangar.domain.events import (
    CapabilityViolationDetected,
    EgressBlocked,
    ProviderCapabilityQuarantined,
    ProviderQuarantined,
)
from mcp_hangar.domain.value_objects.capabilities import (
    ViolationSeverity,
    ViolationType,
)
from mcp_hangar.metrics import CAPABILITY_VIOLATIONS_TOTAL, record_capability_violation
from mcp_hangar.observability.conventions import Enforcement


def _get_counter_value(counter, **labels) -> float:
    """Read the current value of a Counter for the given label set.

    Iterates over collected samples because the project uses a custom
    Counter wrapper (not raw prometheus_client).
    """
    for sample in counter.collect():
        if all(sample.labels.get(k) == v for k, v in labels.items()):
            return sample.value
    return 0.0


class TestCapabilityViolationThroughHandler:
    """CapabilityViolationDetected -> MetricsEventHandler -> Prometheus counter."""

    def test_capability_violation_event_increments_counter(self):
        handler = MetricsEventHandler()
        before = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="test-provider-cv",
            violation_type="capability_drift",
        )

        event = CapabilityViolationDetected(
            provider_id="test-provider-cv",
            violation_type="capability_drift",
            violation_detail="Tool count exceeded declared maximum",
            enforcement_action="alert",
            severity="high",
        )
        handler.handle(event)

        after = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="test-provider-cv",
            violation_type="capability_drift",
        )
        assert after - before == 1.0, f"Expected counter to increment by 1, got delta {after - before}"


class TestEgressBlockedThroughHandler:
    """EgressBlocked -> MetricsEventHandler -> counter with violation_type=egress_denied."""

    def test_egress_blocked_event_increments_counter_with_egress_denied(self):
        handler = MetricsEventHandler()
        before = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="test-provider-eb",
            violation_type="egress_denied",
        )

        event = EgressBlocked(
            provider_id="test-provider-eb",
            destination_host="evil.example.com",
            destination_port=443,
            protocol="https",
        )
        handler.handle(event)

        after = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="test-provider-eb",
            violation_type="egress_denied",
        )
        assert after - before == 1.0, f"Expected counter to increment by 1, got delta {after - before}"


class TestViolationTypeEnumRoundtrip:
    """ViolationType enum .value used in event field survives handler dispatch."""

    def test_violation_type_enum_value_roundtrips_through_event(self):
        handler = MetricsEventHandler()
        vtype = ViolationType.UNDECLARED_TOOL

        before = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="test-provider-rt",
            violation_type=vtype.value,
        )

        event = CapabilityViolationDetected(
            provider_id="test-provider-rt",
            violation_type=vtype.value,
            violation_detail="Undeclared tool detected",
            enforcement_action="block",
            severity=ViolationSeverity.MEDIUM.value,
        )
        handler.handle(event)

        after = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="test-provider-rt",
            violation_type=vtype.value,
        )
        assert after - before == 1.0


class TestViolationSeverityFieldOnEvent:
    """CapabilityViolationDetected has severity field from ViolationSeverity."""

    def test_severity_field_accessible_as_string(self):
        event = CapabilityViolationDetected(
            provider_id="test-provider-sev",
            violation_type="schema_mismatch",
            violation_detail="Schema changed",
            enforcement_action="alert",
            severity=ViolationSeverity.CRITICAL.value,
        )
        assert event.severity == "critical"
        assert isinstance(event.severity, str)

    def test_severity_default_is_high(self):
        event = CapabilityViolationDetected(
            provider_id="test-provider-def",
            violation_type="capability_drift",
            violation_detail="Drift detected",
            enforcement_action="alert",
        )
        assert event.severity == "high"


class TestOtelEnforcementAttributes:
    """All Enforcement attributes follow mcp.enforcement.* naming convention."""

    EXPECTED_ATTRIBUTES = [
        "VIOLATION_TYPE",
        "ACTION",
        "EGRESS_DESTINATION",
        "VIOLATION_COUNT",
        "VIOLATION_SEVERITY",
        "POLICY_RESULT",
        "POLICY_NAME",
    ]

    def test_all_enforcement_attributes_follow_namespace_convention(self):
        for attr_name in self.EXPECTED_ATTRIBUTES:
            value = getattr(Enforcement, attr_name)
            assert value.startswith("mcp.enforcement."), (
                f"Enforcement.{attr_name} = {value!r} does not follow mcp.enforcement.* naming convention"
            )

    def test_violation_severity_attribute_value(self):
        """Enforcement.VIOLATION_SEVERITY == 'mcp.enforcement.violation_severity'."""
        assert Enforcement.VIOLATION_SEVERITY == "mcp.enforcement.violation_severity"

    def test_violation_type_attribute_value(self):
        assert Enforcement.VIOLATION_TYPE == "mcp.enforcement.violation_type"

    def test_action_attribute_value(self):
        assert Enforcement.ACTION == "mcp.enforcement.action"

    def test_egress_destination_attribute_value(self):
        assert Enforcement.EGRESS_DESTINATION == "mcp.enforcement.egress_destination"

    def test_violation_count_attribute_value(self):
        assert Enforcement.VIOLATION_COUNT == "mcp.enforcement.violation_count"


class TestQuarantinedEventsDistinct:
    """ProviderCapabilityQuarantined is not ProviderQuarantined (different classes)."""

    def test_different_class_identity(self):
        assert ProviderCapabilityQuarantined is not ProviderQuarantined

    def test_different_field_signatures(self):
        cap_q = ProviderCapabilityQuarantined(
            provider_id="test-provider",
            reason="Too many violations",
            violation_count=5,
        )
        disc_q = ProviderQuarantined(
            provider_name="test-provider",
            source_type="kubernetes",
            reason="Health check failed",
            validation_result="failed",
        )
        # ProviderCapabilityQuarantined has provider_id; ProviderQuarantined has provider_name
        assert hasattr(cap_q, "provider_id")
        assert hasattr(cap_q, "violation_count")
        assert hasattr(disc_q, "provider_name")
        assert hasattr(disc_q, "source_type")
        assert not hasattr(cap_q, "provider_name")
        assert not hasattr(disc_q, "provider_id")


class TestMultipleProvidersIndependentCounters:
    """Violations for different providers increment independently."""

    def test_independent_counter_increments(self):
        handler = MetricsEventHandler()

        before_a = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="provider-alpha",
            violation_type="capability_drift",
        )
        before_b = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="provider-beta",
            violation_type="capability_drift",
        )

        event_a = CapabilityViolationDetected(
            provider_id="provider-alpha",
            violation_type="capability_drift",
            violation_detail="Drift A",
            enforcement_action="alert",
        )
        handler.handle(event_a)

        after_a = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="provider-alpha",
            violation_type="capability_drift",
        )
        after_b = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="provider-beta",
            violation_type="capability_drift",
        )

        assert after_a - before_a == 1.0, "provider-alpha counter should increment by 1"
        assert after_b - before_b == 0.0, "provider-beta counter should not change"

    def test_same_violation_type_different_providers(self):
        handler = MetricsEventHandler()

        before_a = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="provider-gamma",
            violation_type="egress_denied",
        )
        before_b = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="provider-delta",
            violation_type="egress_denied",
        )

        event_a = EgressBlocked(
            provider_id="provider-gamma",
            destination_host="a.example.com",
            destination_port=443,
            protocol="https",
        )
        event_b = EgressBlocked(
            provider_id="provider-delta",
            destination_host="b.example.com",
            destination_port=80,
            protocol="http",
        )
        handler.handle(event_a)
        handler.handle(event_b)

        after_a = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="provider-gamma",
            violation_type="egress_denied",
        )
        after_b = _get_counter_value(
            CAPABILITY_VIOLATIONS_TOTAL,
            provider="provider-delta",
            violation_type="egress_denied",
        )

        assert after_a - before_a == 1.0
        assert after_b - before_b == 1.0
