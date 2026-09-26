"""Every L7 header check fails closed on the served app (#1599, ADR-025).

Over the real streamable-HTTP transport (``_front_door_harness``):

- a front-door call carrying the declared ``Mcp-Param-Region`` beside an
  undeclared ``Mcp-Param-Tier`` is decided by the tool rules, and the
  ``EgressPolicyEnforced`` record says a header rule was not consulted. Before
  #1599 the reasons were silent: the tier header was dropped and matched
  nothing;
- under ``headers.param_validation.required``, a ``hangar_call`` carrying an
  ``Mcp-Param-*`` header is refused with ``HEADER_MISMATCH`` and never reaches
  its upstream, and the same call without the header is served. Before #1599
  ``required`` read only the front door's listing-failure mark;
- under ``required`` the mixed front-door call is refused too.

Naming: neutral placeholders only (store, read_item, tenant:a).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

from mcp_hangar.domain.policies.egress_l7 import (
    HEADER_RULES_NOT_CONSULTED,
    HeaderMatch,
    HeaderRules,
    L7Policy,
    ToolAction,
    ToolRules,
)
from mcp_hangar.fastmcp_server import flat_tool_projection
from mcp_hangar.tasks_wire import HEADER_MISMATCH
from tests.integration._front_door_harness import MODERN, SERVER, TENANT_A, Upstream, front_door, jsonrpc

TOOL = "read_item"
REGION = "eu-west-1"
#: A tool-name default-deny, and a header allow on the undeclared tier that must not outrank it.
#: The deny is what leaves an ``EgressPolicyEnforced`` record with the reasons.
POLICY = L7Policy(
    tools=ToolRules(allow=("other_tool",)),
    headers=HeaderRules(allow=(HeaderMatch(name="Mcp-Param-Tier", values=("*",)),)),
    default_action=ToolAction.DENY,
)


class _RegionUpstream(Upstream):
    properties: ClassVar[dict[str, Any]] = {"region": {"type": "string", "x-mcp-header": "Region"}}


@pytest.fixture
def required() -> Iterator[None]:
    before = flat_tool_projection.param_validation_required()
    flat_tool_projection.set_param_validation_required(True)
    yield
    flat_tool_projection.set_param_validation_required(before)


def _set(policy: L7Policy) -> None:
    from mcp_hangar.server.context import get_context

    get_context().get_mcp_server(SERVER).set_l7_policy(policy)


def _enforced() -> list[Any]:
    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import EgressPolicyEnforced
    from mcp_hangar.server.context import get_context

    seen: list[Any] = []
    get_context().event_bus.subscribe(EgressPolicyEnforced, seen.append, kind=HandlerKind.PROJECTION)
    return seen


def _mixed_call(door: Any) -> Any:
    return door.call(
        TENANT_A,
        TOOL,
        {"region": REGION},
        era=MODERN,
        extra_headers={"Mcp-Param-Region": REGION, "Mcp-Param-Tier": "gold"},
    )


def _hangar_call(door: Any, extra_headers: dict[str, str] | None) -> Any:
    calls = [{"mcp_server": SERVER, "tool": TOOL, "arguments": {"region": REGION}}]
    return door.call(TENANT_A, "hangar_call", {"calls": calls}, era=MODERN, extra_headers=extra_headers)


def test_a_mixed_front_door_request_says_a_header_rule_was_not_consulted() -> None:
    with front_door((TOOL,), topology="front_door", upstream_class=_RegionUpstream) as door:
        _set(POLICY)
        enforced = _enforced()
        _mixed_call(door)
        assert door.upstream.called == [], "a call the tool rules deny reached its upstream"

    (event,) = enforced
    assert event.rule_kind == "tool"
    assert HEADER_RULES_NOT_CONSULTED in event.reasons
    assert not any("gold" in reason for reason in event.reasons)


def test_a_fully_checked_front_door_request_says_nothing_new() -> None:
    with front_door((TOOL,), topology="front_door", upstream_class=_RegionUpstream) as door:
        _set(POLICY)
        enforced = _enforced()
        door.call(TENANT_A, TOOL, {"region": REGION}, era=MODERN, extra_headers={"Mcp-Param-Region": REGION})

    (event,) = enforced
    assert HEADER_RULES_NOT_CONSULTED not in event.reasons


def test_required_refuses_hangar_call_carrying_a_param_header(required) -> None:
    with front_door((TOOL,), topology="egress", upstream_class=_RegionUpstream) as door:
        payload = jsonrpc(_hangar_call(door, {"Mcp-Param-Tier": "gold"}))
        assert door.upstream.called == [], "a refused call reached its upstream"

    assert payload.get("error", {}).get("code") == HEADER_MISMATCH, payload
    assert "could not be validated" in payload["error"]["message"]
    assert "gold" not in payload["error"]["message"]


def test_required_serves_hangar_call_without_one(required) -> None:
    with front_door((TOOL,), topology="egress", upstream_class=_RegionUpstream) as door:
        payload = jsonrpc(_hangar_call(door, None))
        assert door.upstream.called == [TOOL]

    assert "result" in payload, payload


def test_required_refuses_the_mixed_front_door_request(required) -> None:
    with front_door((TOOL,), topology="front_door", upstream_class=_RegionUpstream) as door:
        payload = jsonrpc(_mixed_call(door))
        assert door.upstream.called == []

    assert payload.get("error", {}).get("code") == HEADER_MISMATCH, payload


def test_required_serves_a_fully_checked_front_door_request(required) -> None:
    with front_door((TOOL,), topology="front_door", upstream_class=_RegionUpstream) as door:
        payload = jsonrpc(
            door.call(TENANT_A, TOOL, {"region": REGION}, era=MODERN, extra_headers={"Mcp-Param-Region": REGION})
        )
        assert door.upstream.called == [TOOL]

    assert "result" in payload, payload
