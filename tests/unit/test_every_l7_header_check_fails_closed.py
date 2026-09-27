"""Every L7 header check fails closed on an unknown validation state (#1599, ADR-025).

Three defaults stayed fail-open after #1597:

- a header mapping with no validation key was read as validated;
- a request mixing a checked ``Mcp-Param-*`` header with a dropped one kept
  the key at ``ran``, so the verdict never said a rule was not consulted;
- ``headers.param_validation.required`` read only the front door's
  listing-failure mark, so a ``hangar_call`` carrying ``Mcp-Param-*`` headers,
  none of them ever checked, was served.

The binding now states a drop as ``partial``: the checked headers still reach
the selector, and the reasons say some did not. ``required`` refuses any
request carrying an ``Mcp-Param-*`` header that did not reach the selector.

Naming: neutral placeholders only (read_item, tenant:a).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.context import (
    PARAM_VALIDATED_HEADERS_ATTR,
    PARAM_VALIDATION_KEY,
    PARAM_VALIDATION_PARTIAL,
    PARAM_VALIDATION_RAN,
    PARAM_VALIDATION_STATE_ATTR,
    bind_routing_headers,
    param_headers_unchecked,
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
    evaluate_headers,
)
from mcp_hangar.fastmcp_server import flat_tool_projection as ftp
from mcp_hangar.server.tools.batch import _refuse_if_param_headers_unchecked
from mcp_hangar.tasks_wire import HEADER_MISMATCH

MODERN = "2026-07-28"
TOOL = "read_item"
EU = HeaderMatch(name="Mcp-Param-Region", values=("eu-*",))
GOLD = HeaderMatch(name="Mcp-Param-Tier", values=("gold",))
MIXED = {"mcp-param-region": "eu-west-1", "mcp-param-tier": "gold", "mcp-protocol-version": MODERN}


def _ctx(headers: dict[str, str], **state: Any) -> SimpleNamespace:
    body = json.dumps({"method": "tools/call", "params": {"name": TOOL, "arguments": {"region": "eu-west-1"}}})
    request = SimpleNamespace(headers=headers, state=SimpleNamespace(**state), _body=body.encode())
    return SimpleNamespace(request=request)


def _bound(ctx: SimpleNamespace) -> dict[str, str]:
    token = bind_routing_headers(ctx)
    try:
        return dict(routing_headers_var.get() or {})
    finally:
        release_routing_headers(token)


def _mixed() -> dict[str, str]:
    """The front door's mixed request: the region was checked, the tier was not."""
    return _bound(_ctx(MIXED, **{PARAM_VALIDATED_HEADERS_ATTR: frozenset({"mcp-param-region"})}))


@pytest.fixture(autouse=True)
def default_off():
    before = ftp.param_validation_required()
    yield
    ftp.set_param_validation_required(before)


class TestAMissingKeyIsNotAPass:
    def test_no_key_consults_no_header_rule(self) -> None:
        without_key = {"mcp-param-region": "eu-west-1", "mcp-protocol-version": MODERN}

        assert evaluate_headers(without_key, HeaderRules(deny=(EU,))) is None

    def test_an_unknown_value_is_not_a_pass_either(self) -> None:
        headers = {"mcp-param-region": "eu-west-1", "mcp-protocol-version": MODERN, PARAM_VALIDATION_KEY: "yes"}

        assert evaluate_headers(headers, HeaderRules(deny=(EU,))) is None


class TestAMixedRequest:
    def test_the_drop_is_stated_without_naming_what_was_dropped(self) -> None:
        bound = _mixed()

        assert bound[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_PARTIAL
        assert "mcp-param-tier" not in bound
        assert "gold" not in bound.values()

    def test_the_verdict_says_a_rule_was_not_consulted(self) -> None:
        policy = L7Policy(tools=ToolRules(allow=("*",)), headers=HeaderRules(deny=(GOLD,)))

        decision = evaluate(TOOL, {}, policy, _mixed())

        assert (decision.action, decision.rule_kind) == (ToolAction.ALLOW, "tool")
        assert HEADER_RULES_NOT_CONSULTED in decision.reasons
        assert not any("gold" in r or "Tier" in r for r in decision.reasons)

    def test_the_checked_header_still_decides(self) -> None:
        """Not consulting the checked header either would let a stray header void a deny."""
        policy = L7Policy(tools=ToolRules(allow=("*",)), headers=HeaderRules(deny=(EU,)))

        decision = evaluate(TOOL, {}, policy, _mixed())

        assert (decision.action, decision.rule_kind) == (ToolAction.DENY, "header")
        assert HEADER_RULES_NOT_CONSULTED in decision.reasons

    def test_a_fully_checked_request_says_nothing_new(self) -> None:
        checked = frozenset({"mcp-param-region", "mcp-param-tier"})
        bound = _bound(_ctx(MIXED, **{PARAM_VALIDATED_HEADERS_ATTR: checked}))
        policy = L7Policy(tools=ToolRules(allow=("*",)), headers=HeaderRules(deny=(EU,)))

        assert bound[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_RAN
        assert HEADER_RULES_NOT_CONSULTED not in evaluate(TOOL, {}, policy, bound).reasons


class TestWhatRequiredRefuses:
    @pytest.mark.parametrize(
        ("headers", "state", "unchecked"),
        [
            (MIXED, {PARAM_VALIDATED_HEADERS_ATTR: frozenset({"mcp-param-region"})}, True),
            (MIXED, {}, True),
            (MIXED, {PARAM_VALIDATED_HEADERS_ATTR: frozenset(MIXED) - {"mcp-protocol-version"}}, False),
            (MIXED, {PARAM_VALIDATED_HEADERS_ATTR: frozenset(MIXED), PARAM_VALIDATION_STATE_ATTR: True}, True),
            ({"mcp-protocol-version": MODERN}, {}, False),
            ({}, {}, False),
        ],
        ids=["mixed", "none-checked", "all-checked", "skip-recorded", "version-only", "no-headers"],
    )
    def test_an_unchecked_param_header_is_what_counts(self, headers, state, unchecked) -> None:
        assert param_headers_unchecked(_ctx(headers, **state)) is unchecked

    def test_no_request_has_nothing_unchecked(self) -> None:
        """stdio and the embedded call carry no request, so no header."""
        assert param_headers_unchecked(None) is False

    def test_an_unreadable_request_counts_as_unchecked(self) -> None:
        class _Exploding:
            @property
            def request(self) -> Any:
                raise RuntimeError("no request")

        assert param_headers_unchecked(_Exploding()) is True

    def test_hangar_call_with_a_param_header_is_refused(self) -> None:
        ftp.set_param_validation_required(True)
        ctx = SimpleNamespace(request_context=_ctx({"mcp-param-tier": "gold", "mcp-protocol-version": MODERN}))

        with pytest.raises(Exception) as caught:
            _refuse_if_param_headers_unchecked(ctx)  # type: ignore[arg-type]

        assert getattr(caught.value, "code", None) == HEADER_MISMATCH
        assert "could not be validated" in str(caught.value)
        assert "gold" not in str(caught.value)

    def test_hangar_call_without_one_is_not(self) -> None:
        ftp.set_param_validation_required(True)
        ctx = SimpleNamespace(request_context=_ctx({"mcp-protocol-version": MODERN}))

        _refuse_if_param_headers_unchecked(ctx)  # type: ignore[arg-type]

    def test_hangar_call_is_served_by_default(self) -> None:
        ctx = SimpleNamespace(request_context=_ctx({"mcp-param-tier": "gold", "mcp-protocol-version": MODERN}))

        _refuse_if_param_headers_unchecked(ctx)  # type: ignore[arg-type]
