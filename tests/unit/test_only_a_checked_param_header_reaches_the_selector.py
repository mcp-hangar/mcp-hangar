"""Only an ``Mcp-Param-*`` header checked against the body reaches an L7 selector (#1597, ADR-025).

The SDK compares a ``tools/call``'s ``Mcp-Param-*`` headers with its body only
for the headers the called tool declares with ``x-mcp-header``
(``mcp.shared.inbound.validate_mcp_param_headers``). Any other header passes
it unread. ``hangar_call`` declares none, so on that surface nothing was ever
checked, yet the binding used to say it had been, and a header ``allow`` rule
could then outrank a tool-name default-deny.

The binding is now fail-closed: a header is bound only when the request records
it as checked, and only the front door records anything, per declared header.

Naming: neutral placeholders only (read_item, tenant:a).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar._sdk_compat import Tool as MCPTool
from mcp_hangar.context import (
    PARAM_VALIDATED_HEADERS_ATTR,
    PARAM_VALIDATION_KEY,
    PARAM_VALIDATION_RAN,
    PARAM_VALIDATION_SKIPPED,
    PARAM_VALIDATION_STATE_ATTR,
    bind_routing_headers,
    release_routing_headers,
    routing_headers_var,
)
from mcp_hangar.domain.policies.egress_l7 import (
    HEADER_RULES_NOT_CONSULTED,
    HeaderMatch,
    HeaderRules,
    L7Policy,
    ToolAction,
    ToolRules,
    evaluate,
)
from mcp_hangar.fastmcp_server import flat_tool_projection as ftp

MODERN = "2026-07-28"
TOOL = "read_item"
SCHEMA = {"type": "object", "properties": {"region": {"type": "string", "x-mcp-header": "Region"}}}
HEADERS = {"mcp-param-region": "eu-west-1", "mcp-param-tier": "gold", "mcp-protocol-version": MODERN}


def _ctx(headers: dict[str, str] | None = None, **state: Any) -> SimpleNamespace:
    body = json.dumps({"method": "tools/call", "params": {"name": TOOL, "arguments": {"region": "eu-west-1"}}})
    request = SimpleNamespace(
        headers=HEADERS if headers is None else headers, state=SimpleNamespace(**state), _body=body.encode()
    )
    return SimpleNamespace(request=request)


def _bound(ctx: SimpleNamespace) -> dict[str, str]:
    token = bind_routing_headers(ctx)
    try:
        return dict(routing_headers_var.get() or {})
    finally:
        release_routing_headers(token)


class TestTheBindingDefault:
    def test_nothing_recorded_binds_no_param_header_and_says_skipped(self) -> None:
        bound = _bound(_ctx())

        assert [k for k in bound if k.startswith("mcp-param-")] == []
        assert bound[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_SKIPPED
        assert bound["mcp-protocol-version"] == MODERN

    def test_a_recorded_header_is_bound_and_an_unrecorded_one_is_not(self) -> None:
        """Per header: a declared, checked header does not vouch for a neighbour nobody checked."""
        bound = _bound(_ctx(**{PARAM_VALIDATED_HEADERS_ATTR: frozenset({"mcp-param-region"})}))

        assert bound["mcp-param-region"] == "eu-west-1"
        assert "mcp-param-tier" not in bound
        assert bound[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_RAN

    def test_a_recorded_skip_voids_the_record(self) -> None:
        ctx = _ctx(**{PARAM_VALIDATED_HEADERS_ATTR: frozenset({"mcp-param-region"}), PARAM_VALIDATION_STATE_ATTR: True})

        bound = _bound(ctx)

        assert "mcp-param-region" not in bound
        assert bound[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_SKIPPED

    def test_a_request_with_no_param_header_has_nothing_to_have_skipped(self) -> None:
        assert _bound(_ctx({"mcp-protocol-version": MODERN}))[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_RAN


class TestWhatTheFrontDoorRecords:
    def test_the_listing_keeps_the_called_tools_schema(self) -> None:
        ctx = _ctx()

        ftp._observe_param_header_skips(ctx, [MCPTool(name=TOOL, inputSchema=SCHEMA)], [])

        assert getattr(ctx.request.state, ftp._CHECKED_SCHEMA_ATTR) == SCHEMA

    def test_an_invalid_annotation_keeps_nothing(self) -> None:
        """The SDK checks nothing against a schema whose annotations are invalid."""
        ctx = _ctx()
        invalid = {"type": "object", "properties": {"region": {"type": "object", "x-mcp-header": "Region"}}}

        ftp._observe_param_header_skips(ctx, [MCPTool(name=TOOL, inputSchema=invalid)], [])

        assert not hasattr(ctx.request.state, ftp._CHECKED_SCHEMA_ATTR)

    def test_only_the_declared_header_is_validated(self) -> None:
        ctx = _ctx(**{ftp._CHECKED_SCHEMA_ATTR: SCHEMA})

        assert ftp._validated_param_headers(ctx, {"region": "eu-west-1"}) == frozenset({"mcp-param-region"})

    @pytest.mark.parametrize(
        ("state", "arguments"),
        [
            ({}, {"region": "eu-west-1"}),  # no schema: the listing never found the tool
            ({ftp._CHECKED_SCHEMA_ATTR: SCHEMA}, {"region": "us-east-1"}),  # header and body disagree
            ({ftp._CHECKED_SCHEMA_ATTR: SCHEMA, PARAM_VALIDATION_STATE_ATTR: True}, {"region": "eu-west-1"}),
        ],
        ids=["no-schema", "mismatch", "skip-recorded"],
    )
    def test_anything_short_of_a_passing_check_validates_nothing(self, state, arguments) -> None:
        assert ftp._validated_param_headers(_ctx(**state), arguments) == frozenset()

    def test_a_failure_records_nothing(self) -> None:
        ctx = _ctx(**{ftp._CHECKED_SCHEMA_ATTR: SCHEMA})

        ftp._record_validated_param_headers(ctx, object())  # not a mapping: the check sees no body

        assert getattr(ctx.request.state, PARAM_VALIDATED_HEADERS_ATTR) == frozenset()


def test_an_unchecked_header_allow_does_not_outrank_a_tool_default_deny() -> None:
    """The defect, at the evaluator: the tool rules decide and the reason says why."""
    policy = L7Policy(
        tools=ToolRules(allow=("other",)),
        headers=HeaderRules(allow=(HeaderMatch(name="Mcp-Param-Tier", values=("*",)),)),
        default_action=ToolAction.DENY,
    )

    decision = evaluate(TOOL, {}, policy, _bound(_ctx()))

    assert (decision.action, decision.rule_kind) == (ToolAction.DENY, "tool")
    assert HEADER_RULES_NOT_CONSULTED in decision.reasons
