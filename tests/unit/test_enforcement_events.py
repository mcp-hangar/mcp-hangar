"""Unit tests for capability enforcement domain events."""

from mcp_hangar.domain.events import (
    CapabilityDeclarationMissing,
    CapabilityViolationDetected,
    EgressBlocked,
    ProviderCapabilityQuarantined,
    ProviderCapabilityQuarantineReleased,
    ToolSchemaDriftDetected,
)


class TestCapabilityViolationDetected:
    def test_creates_with_required_fields(self) -> None:
        event = CapabilityViolationDetected(
            provider_id="math",
            violation_type="egress_undeclared",
            violation_detail="Connection to 192.168.1.100:9200 not in capabilities",
            enforcement_action="alert",
        )
        assert event.provider_id == "math"
        assert event.violation_type == "egress_undeclared"
        assert event.enforcement_action == "alert"
        assert event.destination is None

    def test_with_destination(self) -> None:
        event = CapabilityViolationDetected(
            provider_id="fetch",
            violation_type="egress_undeclared",
            violation_detail="Blocked",
            enforcement_action="block",
            destination="evil.example.com:443",
        )
        assert event.destination == "evil.example.com:443"

    def test_has_event_id(self) -> None:
        event = CapabilityViolationDetected(
            provider_id="p",
            violation_type="tool_schema_drift",
            violation_detail="Tool 'exec' appeared",
            enforcement_action="quarantine",
        )
        assert event.event_id is not None
        assert len(event.event_id) > 0


class TestEgressBlocked:
    def test_creates_with_defaults(self) -> None:
        event = EgressBlocked(
            provider_id="code-interpreter",
            destination_host="exfil.attacker.com",
            destination_port=443,
            protocol="https",
        )
        assert event.enforcement_source == "networkpolicy"
        assert event.destination_port == 443

    def test_custom_enforcement_source(self) -> None:
        event = EgressBlocked(
            provider_id="p",
            destination_host="192.168.1.1",
            destination_port=22,
            protocol="tcp",
            enforcement_source="iptables",
        )
        assert event.enforcement_source == "iptables"


class TestProviderCapabilityQuarantined:
    def test_creates_with_reason(self) -> None:
        event = ProviderCapabilityQuarantined(
            provider_id="rogue-provider",
            reason="3 egress violations in 60s",
            violation_count=3,
        )
        assert event.violation_count == 3
        assert "egress" in event.reason


class TestProviderCapabilityQuarantineReleased:
    def test_creates_with_operator(self) -> None:
        event = ProviderCapabilityQuarantineReleased(
            provider_id="rogue-provider",
            released_by="ops@example.com",
        )
        assert event.released_by == "ops@example.com"


class TestToolSchemaDriftDetected:
    def test_new_tool_added(self) -> None:
        event = ToolSchemaDriftDetected(
            provider_id="code-runner",
            tools_added=["exec_shell"],
            tools_removed=[],
            tools_changed=[],
        )
        assert "exec_shell" in event.tools_added
        assert event.tools_removed == []

    def test_tool_removed(self) -> None:
        event = ToolSchemaDriftDetected(
            provider_id="math",
            tools_added=[],
            tools_removed=["multiply"],
            tools_changed=["add"],
        )
        assert "multiply" in event.tools_removed
        assert "add" in event.tools_changed


class TestCapabilityDeclarationMissing:
    def test_default_alert_mode(self) -> None:
        event = CapabilityDeclarationMissing(provider_id="legacy-server")
        assert event.enforcement_mode == "alert"

    def test_block_mode(self) -> None:
        event = CapabilityDeclarationMissing(
            provider_id="untrusted-server",
            enforcement_mode="block",
        )
        assert event.enforcement_mode == "block"
