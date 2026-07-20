"""Unit tests for MCP OTEL semantic conventions."""

from mcp_hangar.observability.conventions import (
    Audit,
    Behavioral,
    Caller,
    Cost,
    Enforcement,
    GenAI,
    Health,
    MCP,
    Metrics,
    McpServer,
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


class TestConventionUniqueness:
    """Attribute names must be unique across all convention classes."""

    def test_no_duplicate_attribute_names(self) -> None:
        all_attrs: list[str] = []
        for cls in (McpServer, MCP, Enforcement, Audit, Behavioral, Health, Caller, Cost):
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


class TestMetricNames:
    def test_capability_violations_metric(self) -> None:
        assert Metrics.CAPABILITY_VIOLATIONS_TOTAL == "mcp_hangar_capability_violations_total"


class TestSetGovernanceAttributes:
    """Tests for the set_governance_attributes convenience helper."""

    def test_sets_required_provider_and_tool_attributes(self) -> None:
        """set_governance_attributes sets provider.id and tool.name."""
        from unittest.mock import MagicMock

        from mcp_hangar.observability.conventions import set_governance_attributes

        span = MagicMock()
        set_governance_attributes(span, mcp_server_id="math", tool_name="add")

        calls = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
        assert calls[McpServer.ID] == "math"
        assert calls[GenAI.TOOL_NAME] == "add"

    def test_does_not_set_none_values(self) -> None:
        """None arguments must not produce empty span attributes."""
        from unittest.mock import MagicMock

        from mcp_hangar.observability.conventions import set_governance_attributes

        span = MagicMock()
        set_governance_attributes(span, mcp_server_id="p", tool_name="t", user_id=None, session_id=None)

        set_keys = {call.args[0] for call in span.set_attribute.call_args_list}
        assert MCP.USER_ID not in set_keys
        assert MCP.SESSION_ID not in set_keys

    def test_sets_optional_identity_attributes_when_provided(self) -> None:
        from unittest.mock import MagicMock

        from mcp_hangar.observability.conventions import set_governance_attributes

        span = MagicMock()
        set_governance_attributes(
            span,
            mcp_server_id="p",
            tool_name="t",
            user_id="alice",
            session_id="sess-1",
            group_id="group-a",
        )
        calls = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
        assert calls[MCP.USER_ID] == "alice"
        assert calls[MCP.SESSION_ID] == "sess-1"
        assert calls[McpServer.GROUP_ID] == "group-a"

    def test_sets_enforcement_attributes_when_provided(self) -> None:
        from unittest.mock import MagicMock

        from mcp_hangar.observability.conventions import set_governance_attributes

        span = MagicMock()
        set_governance_attributes(
            span,
            mcp_server_id="p",
            tool_name="t",
            policy_result="deny",
            enforcement_action="block",
        )
        calls = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
        assert calls[Enforcement.POLICY_RESULT] == "deny"
        assert calls[Enforcement.ACTION] == "block"

    def test_sets_caller_attributes_when_provided(self) -> None:
        from unittest.mock import MagicMock

        from mcp_hangar.observability.conventions import set_governance_attributes

        span = MagicMock()
        set_governance_attributes(
            span,
            mcp_server_id="p",
            tool_name="t",
            caller_type="human",
            caller_id="alice",
            caller_roles="admin,viewer",
        )
        calls = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
        assert calls[Caller.TYPE] == "human"
        assert calls[Caller.ID] == "alice"
        assert calls[Caller.ROLES] == "admin,viewer"

    def test_sets_cost_attributes_when_provided(self) -> None:
        from unittest.mock import MagicMock

        from mcp_hangar.observability.conventions import set_governance_attributes

        span = MagicMock()
        set_governance_attributes(
            span,
            mcp_server_id="p",
            tool_name="t",
            cost_cents=150,
            cost_model="token",
            cost_input_tokens=500,
            cost_output_tokens=200,
            cost_currency="USD",
        )
        calls = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
        assert calls[Cost.CENTS] == 150
        assert calls[Cost.MODEL] == "token"
        assert calls[GenAI.USAGE_INPUT_TOKENS] == 500
        assert calls[GenAI.USAGE_OUTPUT_TOKENS] == 200
        assert calls[Cost.CURRENCY] == "USD"

    def test_does_not_set_caller_cost_when_none(self) -> None:
        from unittest.mock import MagicMock

        from mcp_hangar.observability.conventions import set_governance_attributes

        span = MagicMock()
        set_governance_attributes(span, mcp_server_id="p", tool_name="t")
        set_keys = {call.args[0] for call in span.set_attribute.call_args_list}
        assert Caller.TYPE not in set_keys
        assert Caller.ID not in set_keys
        assert Cost.CENTS not in set_keys
        assert Cost.MODEL not in set_keys


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
