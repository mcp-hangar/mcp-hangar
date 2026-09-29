"""Unit tests for MCP OTEL semantic conventions."""

from mcp_hangar.observability.conventions import (
    MCP,
    Audit,
    Behavioral,
    Caller,
    Cost,
    Enforcement,
    GenAI,
    Health,
    McpServer,
    Risk,
)


def _public_str_attrs(cls: type) -> list[str]:
    """Return only the public string constants defined on a conventions class."""
    return [v for k, v in vars(cls).items() if isinstance(v, str) and not k.startswith("_")]


class TestConventionNamespacing:
    """Verify all attributes follow mcp.* namespace convention."""

    def test_provider_attributes_prefixed(self) -> None:
        for attr in _public_str_attrs(McpServer):
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

    def test_caller_attributes_prefixed(self) -> None:
        for attr in _public_str_attrs(Caller):
            assert attr.startswith("mcp."), f"{attr} should start with mcp."

    def test_cost_attributes_prefixed(self) -> None:
        for attr in _public_str_attrs(Cost):
            assert attr.startswith("mcp."), f"{attr} should start with mcp."

    def test_risk_attributes_prefixed(self) -> None:
        for attr in _public_str_attrs(Risk):
            assert attr.startswith("mcp.risk."), f"{attr} should start with mcp.risk."


class TestConventionUniqueness:
    """Attribute names must be unique across all convention classes."""

    def test_no_duplicate_attribute_names(self) -> None:
        all_attrs: list[str] = []
        for cls in (McpServer, MCP, Enforcement, Audit, Behavioral, Health, Caller, Cost, Risk):
            all_attrs.extend(_public_str_attrs(cls))

        duplicates = {a for a in all_attrs if all_attrs.count(a) > 1}
        assert not duplicates, f"Duplicate OTEL attribute names found: {duplicates}"


class TestKeyAttributes:
    """Spot-check that key governance attributes are present."""

    def test_mcp_server_id(self) -> None:
        assert McpServer.ID == "mcp.server.id"

    def test_tool_name(self) -> None:
        assert GenAI.TOOL_NAME == "gen_ai.tool.name"

    def test_enforcement_action(self) -> None:
        assert Enforcement.ACTION == "mcp.enforcement.action"

    def test_violation_type(self) -> None:
        assert Enforcement.VIOLATION_TYPE == "mcp.enforcement.violation_type"

    def test_user_id(self) -> None:
        assert MCP.USER_ID == "mcp.user.id"

    def test_session_id(self) -> None:
        assert MCP.SESSION_ID == "mcp.session.id"

    def test_caller_type(self) -> None:
        assert Caller.TYPE == "mcp.caller.type"

    def test_caller_id(self) -> None:
        assert Caller.ID == "mcp.caller.id"

    def test_cost_cents(self) -> None:
        assert Cost.CENTS == "mcp.cost.cents"

    def test_cost_model(self) -> None:
        assert Cost.MODEL == "mcp.cost.model"


class TestTracingUsesConventionConstants:
    """Verify tracing.py imports and uses convention constants (not raw strings)."""

    def test_tracing_imports_conventions(self) -> None:
        import ast
        import pathlib

        src = pathlib.Path("src/mcp_hangar/observability/tracing.py").read_text()
        tree = ast.parse(src)
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        import_strs = [ast.unparse(node) for node in imports]
        assert any("conventions" in imp for imp in import_strs), "tracing.py must import from conventions.py"

    def test_no_raw_mcp_mcp_server_id_string_in_tracing(self) -> None:
        import pathlib

        src = pathlib.Path("src/mcp_hangar/observability/tracing.py").read_text()
        # raw string literal should not appear -- the constant Provider.ID should be used instead
        assert '"mcp.server.id"' not in src, "tracing.py must use Provider.ID constant, not raw string 'mcp.server.id'"

    def test_no_raw_tool_name_string_in_tracing(self) -> None:
        import pathlib

        src = pathlib.Path("src/mcp_hangar/observability/tracing.py").read_text()
        assert '"gen_ai.tool.name"' not in src, (
            "tracing.py must use GenAI.TOOL_NAME constant, not the raw string 'gen_ai.tool.name'"
        )
