"""Unit tests for MCP OTEL semantic conventions."""

from mcp_hangar.observability.conventions import Audit, Behavioral, Enforcement, Health, MCP, Metrics, Provider


def _public_str_attrs(cls: type) -> list[str]:
    """Return only the public string constants defined on a conventions class."""
    return [
        v
        for k, v in vars(cls).items()
        if isinstance(v, str) and not k.startswith("_")
    ]


class TestConventionNamespacing:
    """Verify all attributes follow mcp.* namespace convention."""

    def test_provider_attributes_prefixed(self) -> None:
        for attr in _public_str_attrs(Provider):
            assert attr.startswith("mcp."), f"{attr} should start with mcp."

    def test_mcp_tool_attributes_prefixed(self) -> None:
        for attr in _public_str_attrs(MCP):
            assert attr.startswith("mcp."), f"{attr} should start with mcp."

    def test_enforcement_attributes_prefixed(self) -> None:
        for attr in _public_str_attrs(Enforcement):
            assert attr.startswith("mcp."), f"{attr} should start with mcp."

    def test_audit_attributes_prefixed(self) -> None:
        for attr in _public_str_attrs(Audit):
            assert attr.startswith("mcp."), f"{attr} should start with mcp."

    def test_behavioral_attributes_prefixed(self) -> None:
        for attr in _public_str_attrs(Behavioral):
            assert attr.startswith("mcp."), f"{attr} should start with mcp."

    def test_health_attributes_prefixed(self) -> None:
        for attr in _public_str_attrs(Health):
            assert attr.startswith("mcp."), f"{attr} should start with mcp."


class TestConventionUniqueness:
    """Attribute names must be unique across all convention classes."""

    def test_no_duplicate_attribute_names(self) -> None:
        all_attrs: list[str] = []
        for cls in (Provider, MCP, Enforcement, Audit, Behavioral, Health):
            all_attrs.extend(_public_str_attrs(cls))

        duplicates = {a for a in all_attrs if all_attrs.count(a) > 1}
        assert not duplicates, f"Duplicate OTEL attribute names found: {duplicates}"


class TestKeyAttributes:
    """Spot-check that key governance attributes are present."""

    def test_provider_id(self) -> None:
        assert Provider.ID == "mcp.provider.id"

    def test_tool_name(self) -> None:
        assert MCP.TOOL_NAME == "mcp.tool.name"

    def test_enforcement_action(self) -> None:
        assert Enforcement.ACTION == "mcp.enforcement.action"

    def test_violation_type(self) -> None:
        assert Enforcement.VIOLATION_TYPE == "mcp.enforcement.violation_type"

    def test_user_id(self) -> None:
        assert MCP.USER_ID == "mcp.user.id"

    def test_session_id(self) -> None:
        assert MCP.SESSION_ID == "mcp.session.id"


class TestMetricNames:
    def test_capability_violations_metric(self) -> None:
        assert Metrics.CAPABILITY_VIOLATIONS_TOTAL == "mcp_hangar_capability_violations_total"

    def test_egress_blocked_metric(self) -> None:
        assert Metrics.EGRESS_BLOCKED_TOTAL == "mcp_hangar_egress_blocked_total"

    def test_quarantined_metric(self) -> None:
        assert Metrics.PROVIDERS_QUARANTINED == "mcp_hangar_providers_quarantined"
