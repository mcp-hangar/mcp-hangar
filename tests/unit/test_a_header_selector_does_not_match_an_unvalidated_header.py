"""An L7 header selector must not match a header nobody validated (#1053, ADR-025).

SEP-2243's safety property is header-body agreement: the header carries the
value the call will execute with, so nobody can route on one value and execute
another. The SDK enforces that pre-dispatch and does so fail-open by design --
a `tools/list` that raises means no schema, no check, and dispatch continues.

Since #1064 Hangar routes on those headers itself, so the fail-open arm lands
inside our own policy engine: a modern request whose validation was skipped
would otherwise satisfy an `allow` / `deny` / `requireApproval` selector. It
does not. The request falls through to the tool rules and the policy default,
exactly as a handshake-era request already does, and the audit reason says the
rules were not consulted rather than that none matched.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from mcp_hangar.context import (
    bind_routing_headers,
    PARAM_VALIDATION_KEY,
    PARAM_VALIDATION_RAN,
    PARAM_VALIDATION_SKIPPED,
    PARAM_VALIDATION_STATE_ATTR,
    release_routing_headers,
    routing_headers_var,
    select_routing_headers,
)
from mcp_hangar.domain.exceptions import EgressPolicyDeniedError
from mcp_hangar.domain.policies.egress_l7 import (
    evaluate,
    evaluate_headers,
    HEADER_RULES_NOT_CONSULTED,
    HeaderMatch,
    HeaderRules,
    L7Policy,
    ToolAction,
    ToolRules,
)
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server import flat_tool_projection

MODERN = "2026-07-28"
EU = HeaderMatch(name="Mcp-Param-Region", values=("eu-*",))


def _headers(*, validated: bool = True) -> dict[str, str]:
    return {
        "mcp-param-region": "eu-west-1",
        "mcp-protocol-version": MODERN,
        PARAM_VALIDATION_KEY: PARAM_VALIDATION_RAN if validated else PARAM_VALIDATION_SKIPPED,
    }


def _ctx(*, headers: dict[str, str] | None = None, state: SimpleNamespace | None = None) -> SimpleNamespace:
    """A front-door request context: a tools/call POST carrying an Mcp-Param header."""
    principal = Principal(id=PrincipalId("user:alice"), type=PrincipalType.USER, tenant_id="tenant:a")
    body = json.dumps(
        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "add", "arguments": {"a": 1}}}
    ).encode()
    request = SimpleNamespace(
        state=state or SimpleNamespace(auth=SimpleNamespace(principal=principal)),
        _body=body,
        headers=headers if headers is not None else {"mcp-param-region": "eu-west-1", "mcp-protocol-version": MODERN},
    )
    return SimpleNamespace(request=request)


def _registered() -> dict[str, Any]:
    handlers: dict[str, Any] = {}

    class _Low:
        def add_request_handler(self, method, params_type, handler):
            handlers[method] = handler

    flat_tool_projection.register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low()))
    return handlers


def _drive_failing_listing(monkeypatch, ctx: SimpleNamespace) -> None:
    """Run the pre-dispatch listing the SDK makes on a tools/call, and fail it.

    That is the live gap: the SDK catches, skips validation and dispatches the
    call anyway.
    """

    def _boom(_tenant: str | None) -> dict:
        raise RuntimeError("listing exploded")

    monkeypatch.setattr(flat_tool_projection, "_build_flat_map", _boom)
    monkeypatch.setattr(
        "mcp_hangar.server.tools.tool_permissions.management_tools_for",
        lambda _ctx: set(),
    )
    handlers = _registered()

    async def _run() -> None:
        with pytest.raises(RuntimeError, match="listing exploded"):
            await handlers["tools/list"](ctx, SimpleNamespace())

    anyio.run(_run)


@pytest.fixture()
def bound_headers():
    """Bind and unbind the request-scoped routing headers around one test."""
    tokens = []

    def _bind(headers: dict[str, str] | None) -> None:
        tokens.append(routing_headers_var.set(headers))

    yield _bind
    for token in reversed(tokens):
        routing_headers_var.reset(token)


class TestTheSkipReachesTheEvaluator:
    def test_a_failed_pre_dispatch_listing_marks_the_request(self, monkeypatch) -> None:
        ctx = _ctx()
        _drive_failing_listing(monkeypatch, ctx)

        assert getattr(ctx.request.state, PARAM_VALIDATION_STATE_ATTR, False) is True

    def test_the_mark_travels_on_the_request_not_a_contextvar(self, monkeypatch) -> None:
        """`bind_routing_headers` rebuilds its mapping from the raw headers, so a
        contextvar set out in the listing would be overwritten by the later bind."""
        ctx = _ctx()
        _drive_failing_listing(monkeypatch, ctx)

        token = bind_routing_headers(ctx)
        try:
            assert routing_headers_var.get()[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_SKIPPED
        finally:
            release_routing_headers(token)

    def test_a_request_nothing_marked_is_carried_as_validated(self) -> None:
        token = bind_routing_headers(_ctx())
        try:
            assert routing_headers_var.get()[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_RAN
        finally:
            release_routing_headers(token)

    def test_a_client_cannot_forge_the_status(self, monkeypatch) -> None:
        """The key is not an HTTP header. A request sending it by that name is
        filtered out with everything else a policy may not see."""
        ctx = _ctx(
            headers={
                "mcp-param-region": "eu-west-1",
                "mcp-protocol-version": MODERN,
                PARAM_VALIDATION_KEY: PARAM_VALIDATION_RAN,
            }
        )
        _drive_failing_listing(monkeypatch, ctx)

        assert PARAM_VALIDATION_KEY not in select_routing_headers(ctx.request.headers)
        token = bind_routing_headers(ctx)
        try:
            assert routing_headers_var.get()[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_SKIPPED
        finally:
            release_routing_headers(token)


class TestTheGate:
    def test_a_skipped_validation_is_not_a_match(self) -> None:
        assert evaluate_headers(_headers(validated=False), HeaderRules(deny=(EU,))) is None
        assert evaluate_headers(_headers(validated=False), HeaderRules(allow=(EU,))) is None
        assert evaluate_headers(_headers(validated=False), HeaderRules(require_approval=(EU,))) is None

    def test_the_same_request_validated_does_match(self) -> None:
        """The probe applies: without this the test above passes on a broken selector."""
        verdict = evaluate_headers(_headers(), HeaderRules(deny=(EU,)))

        assert verdict is not None
        assert verdict[0] is ToolAction.DENY

    def test_an_unstated_status_keeps_the_version_gate(self) -> None:
        """The batch surface and stdio reach the evaluator with no request in hand."""
        without_status = {"mcp-param-region": "eu-west-1", "mcp-protocol-version": MODERN}

        assert evaluate_headers(without_status, HeaderRules(deny=(EU,))) is not None
        assert (
            evaluate_headers({**without_status, "mcp-protocol-version": "2025-06-18"}, HeaderRules(deny=(EU,))) is None
        )


class TestTheVerdict:
    def test_the_call_falls_through_to_the_tool_rules(self) -> None:
        """Not an implicit deny: one caller's unvalidated header must not decide
        another caller's request. The policy default stays in charge."""
        policy = L7Policy(tools=ToolRules(allow=("get_user",)), headers=HeaderRules(deny=(EU,)))

        decision = evaluate("get_user", {}, policy, _headers(validated=False))

        assert decision.action is ToolAction.ALLOW

    def test_an_unearned_allow_is_not_granted_either(self) -> None:
        policy = L7Policy(headers=HeaderRules(allow=(EU,)), default_action=ToolAction.DENY)

        decision = evaluate("get_user", {}, policy, _headers(validated=False))

        assert decision.action is ToolAction.DENY

    def test_the_reason_says_the_rules_were_not_consulted(self) -> None:
        """ "No rule matched" and "the rules were not consulted" are different
        verdicts, and the audit record is where an operator can tell them apart."""
        policy = L7Policy(tools=ToolRules(allow=("*",)), headers=HeaderRules(deny=(EU,)))

        decision = evaluate("get_user", {}, policy, _headers(validated=False))

        assert HEADER_RULES_NOT_CONSULTED in decision.reasons

    def test_a_policy_with_no_selectors_says_nothing_new(self) -> None:
        """Nobody who does not write header selectors sees a change."""
        policy = L7Policy(tools=ToolRules(allow=("*",)))

        decision = evaluate("get_user", {}, policy, _headers(validated=False))

        assert decision.action is ToolAction.ALLOW
        assert HEADER_RULES_NOT_CONSULTED not in decision.reasons

    def test_a_validated_request_still_reports_its_selector(self) -> None:
        policy = L7Policy(tools=ToolRules(allow=("*",)), headers=HeaderRules(deny=(EU,)))

        decision = evaluate("get_user", {}, policy, _headers())

        assert decision.action is ToolAction.DENY
        assert HEADER_RULES_NOT_CONSULTED not in decision.reasons


def test_the_denial_a_skipped_header_would_have_written_does_not_reach_the_aggregate(bound_headers) -> None:
    """The whole point, on the path that runs in production."""
    from unittest.mock import Mock

    from mcp_hangar.domain.model.mcp_server import McpServer

    class _Proceeded(Exception):
        """Raised from a stubbed ensure_ready: the call got past the L7 gate."""

    bound_headers(_headers(validated=False))
    policy = L7Policy(tools=ToolRules(allow=("*",)), headers=HeaderRules(deny=(EU,)))
    server = McpServer(mcp_server_id="s", mode="subprocess", command=["echo"], l7_policy=policy)
    server.ensure_ready = Mock(side_effect=_Proceeded())  # type: ignore[method-assign]

    with pytest.raises(_Proceeded):
        server.invoke_tool("get_user", {})

    bound_headers(_headers())
    with pytest.raises(EgressPolicyDeniedError):
        server.invoke_tool("get_user", {})
