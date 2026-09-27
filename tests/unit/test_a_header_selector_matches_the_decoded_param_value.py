"""An L7 header selector globs the ``Mcp-Param-*`` value the SDK validated, not its wire text (#1600, ADR-025).

A client may send a header value in the SEP-2243 sentinel form
``=?base64?<payload>?=``, and must for a value that would not survive an HTTP
field round-trip (non-ASCII, edge whitespace). The SDK decodes that form before
comparing it with the body (``mcp.shared.inbound.validate_mcp_param_headers``,
through ``decode_header_value``). The binding used to hand the raw wire text to
the selector, so a validated ``eu-west-1`` sent as a sentinel did not match an
operator's ``eu-*``.

The binding now decodes a checked header with the SDK's own helper. A sentinel
that does not decode is one the SDK would have refused, so it is dropped as
unchecked: the key reads ``partial`` or ``skipped`` and the verdict carries
``HEADER_RULES_NOT_CONSULTED``.

Naming: neutral placeholders only (read_item).
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.context import (
    PARAM_VALIDATED_HEADERS_ATTR,
    PARAM_VALIDATION_KEY,
    PARAM_VALIDATION_PARTIAL,
    PARAM_VALIDATION_RAN,
    PARAM_VALIDATION_SKIPPED,
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
    evaluate,
)

MODERN = "2026-07-28"
TOOL = "read_item"
POLICY = L7Policy(
    headers=HeaderRules(deny=(HeaderMatch(name="Mcp-Param-Region", values=("eu-*",)),)),
    default_action=ToolAction.ALLOW,
)


def _sentinel(value: str) -> str:
    return f"=?base64?{base64.b64encode(value.encode('utf-8')).decode('ascii')}?="


def _bound(params: dict[str, str]) -> dict[str, str]:
    headers = {**params, "mcp-protocol-version": MODERN}
    state = SimpleNamespace(**{PARAM_VALIDATED_HEADERS_ATTR: frozenset(params)})
    token = bind_routing_headers(SimpleNamespace(request=SimpleNamespace(headers=headers, state=state)))
    try:
        return dict(routing_headers_var.get() or {})
    finally:
        release_routing_headers(token)


def _decide(params: dict[str, str]) -> Any:
    return evaluate(TOOL, {}, POLICY, _bound(params))


@pytest.mark.parametrize("value", ["eu-west-1", "eu-zürich", "eu-west-1 "])
def test_the_sentinel_form_matches_the_same_glob_as_the_plain_form(value: str) -> None:
    plain, encoded = _decide({"mcp-param-region": value.strip()}), _decide({"mcp-param-region": _sentinel(value)})

    assert (plain.action, plain.rule_kind) == (ToolAction.DENY, "header")
    assert (encoded.action, encoded.rule_kind) == (ToolAction.DENY, "header")
    assert HEADER_RULES_NOT_CONSULTED not in encoded.reasons


def test_a_sentinel_is_bound_as_the_value_the_sdk_compared_with_the_body() -> None:
    bound = _bound({"mcp-param-region": _sentinel("eu-west-1")})

    assert bound["mcp-param-region"] == "eu-west-1"
    assert bound[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_RAN


def test_a_plain_value_is_bound_unchanged() -> None:
    bound = _bound({"mcp-param-region": "us-east-1"})

    assert bound["mcp-param-region"] == "us-east-1"
    assert bound[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_RAN
    assert _decide({"mcp-param-region": "us-east-1"}).rule_kind == "tool"


#: Bad base64, non-canonical base64 (non-zero trailing bits), bad UTF-8.
MALFORMED = ["=?base64?not base64!?=", "=?base64?ZXV=?=", "=?base64?" + base64.b64encode(b"eu-\xff").decode() + "?="]


@pytest.mark.parametrize("value", MALFORMED)
def test_a_malformed_sentinel_is_dropped_as_unchecked(value: str) -> None:
    bound = _bound({"mcp-param-region": value})
    decision = _decide({"mcp-param-region": value})

    assert "mcp-param-region" not in bound
    assert bound[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_SKIPPED
    assert decision.rule_kind == "tool"
    assert HEADER_RULES_NOT_CONSULTED in decision.reasons


def test_a_malformed_sentinel_beside_a_good_header_makes_the_mapping_partial() -> None:
    params = {"mcp-param-region": "eu-west-1", "mcp-param-tier": MALFORMED[0]}
    bound = _bound(params)
    decision = _decide(params)

    assert bound[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_PARTIAL
    assert (bound["mcp-param-region"], "mcp-param-tier" in bound) == ("eu-west-1", False)
    assert (decision.action, decision.rule_kind) == (ToolAction.DENY, "header")
    assert HEADER_RULES_NOT_CONSULTED in decision.reasons


def test_a_malformed_sentinel_counts_as_unchecked_for_required() -> None:
    headers = {"mcp-param-region": MALFORMED[0], "mcp-protocol-version": MODERN}
    state = SimpleNamespace(**{PARAM_VALIDATED_HEADERS_ATTR: frozenset({"mcp-param-region"})})

    assert param_headers_unchecked(SimpleNamespace(request=SimpleNamespace(headers=headers, state=state)))
