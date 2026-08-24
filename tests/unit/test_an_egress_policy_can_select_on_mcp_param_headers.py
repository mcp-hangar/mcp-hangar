"""An MCPEgressPolicy can select on `Mcp-Param-*`, and a legacy revision cannot (#1058).

Region, tenant and priority -- SEP-2243's own examples -- are the dimensions
L7 egress wants, and they are on the wire without parsing the body. Matching
them is what keeps this a header matcher rather than DPI.

The version gate is the security half. On a handshake-era revision nothing has
checked that a mirrored header agrees with the body, so a caller can route on
one value and execute another. Such a request never satisfies a selector: it
falls through to the tool rules and the policy default rather than collecting
an allow it did not earn.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from mcp_hangar.context import routing_headers_var, select_routing_headers
from mcp_hangar.domain.exceptions import EgressPolicyApprovalRequiredError, EgressPolicyDeniedError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import (
    evaluate,
    evaluate_headers,
    HeaderMatch,
    HeaderRules,
    L7Policy,
    ToolAction,
    ToolRules,
)

MODERN = "2026-07-28"
HANDSHAKE_ERA = "2025-06-18"

EU = HeaderMatch(name="Mcp-Param-Region", values=("eu-*",))
US = HeaderMatch(name="Mcp-Param-Region", values=("us-*",))


def _headers(region: str, version: str | None = MODERN) -> dict[str, str]:
    out = {"mcp-param-region": region}
    if version is not None:
        out["mcp-protocol-version"] = version
    return out


class _Proceeded(Exception):
    """Raised from a stubbed ensure_ready: the call got past the L7 gate."""


def _server(policy: L7Policy) -> McpServer:
    server = McpServer(mcp_server_id="s", mode="subprocess", command=["echo"], l7_policy=policy)
    server.ensure_ready = Mock(side_effect=_Proceeded())  # type: ignore[method-assign]
    return server


@pytest.fixture()
def bound_headers():
    """Bind and unbind the request-scoped routing headers around one test."""
    tokens = []

    def _bind(headers: dict[str, str] | None) -> None:
        tokens.append(routing_headers_var.set(headers))

    yield _bind
    for token in reversed(tokens):
        routing_headers_var.reset(token)


class TestTheWireShape:
    def test_a_headers_block_round_trips(self) -> None:
        policy = L7Policy(
            tools=ToolRules(allow=("*",)),
            headers=HeaderRules(deny=(US,), require_approval=(HeaderMatch("Mcp-Param-Tier", ("free",)),)),
        )

        assert L7Policy.from_dict(policy.to_wire()) == policy

    def test_a_policy_with_no_headers_block_is_unchanged(self) -> None:
        """The operator that has not shipped the field yet keeps working."""
        parsed = L7Policy.from_dict({"tools": {"allow": ["*"]}, "defaultAction": "Deny"})

        assert parsed.headers == HeaderRules()
        assert not parsed.headers

    def test_a_non_param_header_is_refused_at_parse(self) -> None:
        """Selecting on Authorization would make a policy a credential oracle."""
        with pytest.raises(ValueError, match="not an Mcp-Param-\\* header"):
            L7Policy.from_dict({"headers": {"deny": [{"name": "Authorization", "values": ["Bearer *"]}]}})

    def test_a_selector_with_nothing_to_match_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no values to match"):
            L7Policy.from_dict({"headers": {"allow": [{"name": "Mcp-Param-Region", "values": []}]}})

    def test_a_malformed_entry_is_refused(self) -> None:
        with pytest.raises(ValueError, match="entries must be objects"):
            L7Policy.from_dict({"headers": {"deny": ["Mcp-Param-Region"]}})


class TestPrecedence:
    def test_deny_beats_require_approval_beats_allow(self) -> None:
        rules = HeaderRules(allow=(EU,), require_approval=(EU,), deny=(EU,))

        action, reason = evaluate_headers(_headers("eu-west-1"), rules)  # type: ignore[misc]
        assert action is ToolAction.DENY
        assert "Mcp-Param-Region" in reason

    def test_a_value_that_matches_nothing_yields_no_verdict(self) -> None:
        assert evaluate_headers(_headers("ap-south-1"), HeaderRules(deny=(EU,))) is None

    def test_the_header_name_is_matched_case_insensitively(self) -> None:
        rules = HeaderRules(deny=(HeaderMatch("MCP-PARAM-REGION", ("eu-*",)),))

        assert evaluate_headers(_headers("eu-west-1"), rules) is not None

    def test_an_empty_rule_set_never_looks_at_the_headers(self) -> None:
        assert evaluate_headers(_headers("eu-west-1"), HeaderRules()) is None


class TestTheVersionGate:
    def test_a_handshake_era_request_never_satisfies_a_selector(self) -> None:
        headers = _headers("eu-west-1", HANDSHAKE_ERA)

        assert evaluate_headers(headers, HeaderRules(deny=(EU,))) is None
        assert evaluate_headers(headers, HeaderRules(allow=(EU,))) is None

    def test_an_absent_version_header_is_not_modern(self) -> None:
        assert evaluate_headers(_headers("eu-west-1", None), HeaderRules(deny=(EU,))) is None

    def test_unbound_headers_are_not_a_match(self) -> None:
        assert evaluate_headers(None, HeaderRules(deny=(EU,))) is None


class TestTheCallPath:
    """Driven through McpServer.invoke_tool, which is what runs in production."""

    def test_a_matching_deny_selector_blocks_the_call(self, bound_headers) -> None:
        bound_headers(_headers("us-east-1"))
        policy = L7Policy(tools=ToolRules(allow=("*",)), headers=HeaderRules(deny=(US,)))

        with pytest.raises(EgressPolicyDeniedError) as excinfo:
            _server(policy).invoke_tool("get_user", {})

        assert "Mcp-Param-Region" in excinfo.value.reason

    def test_the_same_header_on_a_legacy_revision_does_not_match(self, bound_headers) -> None:
        """The tool rules decide instead -- here, allow. Not a trusted miss: the
        selector simply did not apply, and `*` is what let the call through."""
        bound_headers(_headers("us-east-1", HANDSHAKE_ERA))
        policy = L7Policy(tools=ToolRules(allow=("*",)), headers=HeaderRules(deny=(US,)))

        with pytest.raises(_Proceeded):
            _server(policy).invoke_tool("get_user", {})

    def test_a_legacy_revision_falls_back_to_the_default_action(self, bound_headers) -> None:
        """An allow selector is not a way around defaultAction: Deny either."""
        bound_headers(_headers("eu-west-1", HANDSHAKE_ERA))
        policy = L7Policy(headers=HeaderRules(allow=(EU,)), default_action=ToolAction.DENY)

        with pytest.raises(EgressPolicyDeniedError):
            _server(policy).invoke_tool("get_user", {})

    def test_a_matching_allow_selector_satisfies_a_default_deny(self, bound_headers) -> None:
        bound_headers(_headers("eu-west-1"))
        policy = L7Policy(headers=HeaderRules(allow=(EU,)), default_action=ToolAction.DENY)

        with pytest.raises(_Proceeded):
            _server(policy).invoke_tool("get_user", {})

    def test_a_matching_selector_can_route_to_approval(self, bound_headers) -> None:
        bound_headers(_headers("us-east-1"))
        policy = L7Policy(tools=ToolRules(allow=("*",)), headers=HeaderRules(require_approval=(US,)))

        with pytest.raises(EgressPolicyApprovalRequiredError):
            _server(policy).invoke_tool("get_user", {})

    def test_a_secret_in_the_arguments_still_wins_over_a_header_allow(self, bound_headers) -> None:
        """Deny always wins: a selector is not a way past argument scanning."""
        from mcp_hangar.domain.policies.egress_l7 import ArgumentRules

        bound_headers(_headers("eu-west-1"))
        policy = L7Policy(
            headers=HeaderRules(allow=(EU,)),
            arguments=ArgumentRules(secret_patterns=("aws-keys",)),
        )

        with pytest.raises(EgressPolicyDeniedError) as excinfo:
            _server(policy).invoke_tool("get_user", {"key": "AKIAIOSFODNN7EXAMPLE"})

        assert "aws-keys" in excinfo.value.reason


class TestWhatThePolicyIsAllowedToSee:
    def test_only_param_and_version_headers_are_carried(self) -> None:
        selected = select_routing_headers(
            {
                "Authorization": "Bearer sk-secret",
                "Cookie": "session=abc",
                "Mcp-Param-Region": "eu-west-1",
                "MCP-Protocol-Version": MODERN,
            }
        )

        assert selected == {"mcp-param-region": "eu-west-1", "mcp-protocol-version": MODERN}

    def test_no_headers_selects_nothing(self) -> None:
        assert select_routing_headers(None) == {}


def test_evaluate_reports_the_header_reason(bound_headers) -> None:
    """The audit trail says which selector decided, not just that one did."""
    decision = evaluate("get_user", {}, L7Policy(headers=HeaderRules(deny=(EU,))), _headers("eu-west-1"))

    assert decision.action is ToolAction.DENY
    assert any("Mcp-Param-Region" in r for r in decision.reasons)
