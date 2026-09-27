"""A declared header sent in sentinel form decides like its plain form on the served app (#1600, ADR-025).

Over the real streamable-HTTP transport (``_front_door_harness``), a front-door
call to a tool whose ``region`` declares ``x-mcp-header: Region`` mirrors it in
``Mcp-Param-Region``, once as plain text and once as the SEP-2243 sentinel
``=?base64?<payload>?=``. The SDK decodes the sentinel before comparing it with
the body, so both are validated; a header ``deny`` on ``eu-*`` must then refuse
both, with ``rule_kind=header``. Before #1600 the selector globbed the wire
text, the sentinel matched nothing, and the tool rules served the call.

A value only the sentinel can carry (non-ASCII) is covered too: that is the
form a conforming client must send.

Naming: neutral placeholders only (read_item, tenant:a).
"""

from __future__ import annotations

import base64
from typing import Any, ClassVar

import pytest

from mcp_hangar.domain.policies.egress_l7 import (
    HEADER_RULES_NOT_CONSULTED,
    HeaderMatch,
    HeaderRules,
    L7Policy,
    ToolAction,
)
from tests.integration._front_door_harness import MODERN, SERVER, TENANT_A, Upstream, front_door

TOOL = "read_item"
POLICY = L7Policy(
    headers=HeaderRules(deny=(HeaderMatch(name="Mcp-Param-Region", values=("eu-*",)),)),
    default_action=ToolAction.ALLOW,
)


class _RegionUpstream(Upstream):
    properties: ClassVar[dict[str, Any]] = {"region": {"type": "string", "x-mcp-header": "Region"}}


def _sentinel(value: str) -> str:
    return f"=?base64?{base64.b64encode(value.encode('utf-8')).decode('ascii')}?="


def _decide(region: str, header: str) -> tuple[list[str], Any]:
    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import EgressPolicyEnforced
    from mcp_hangar.server.context import get_context

    with front_door((TOOL,), topology="front_door", upstream_class=_RegionUpstream) as door:
        get_context().get_mcp_server(SERVER).set_l7_policy(POLICY)
        seen: list[Any] = []
        get_context().event_bus.subscribe(EgressPolicyEnforced, seen.append, kind=HandlerKind.PROJECTION)
        door.call(TENANT_A, TOOL, {"region": region}, era=MODERN, extra_headers={"Mcp-Param-Region": header})
        return list(door.upstream.called), seen


@pytest.mark.parametrize(
    ("region", "header"),
    [
        ("eu-west-1", "eu-west-1"),
        ("eu-west-1", _sentinel("eu-west-1")),
        ("eu-zürich", _sentinel("eu-zürich")),
    ],
    ids=["plain", "sentinel", "sentinel-non-ascii"],
)
def test_a_header_deny_refuses_the_value_in_either_form(region: str, header: str) -> None:
    called, enforced = _decide(region, header)

    assert called == [], "a call the header rule denies reached its upstream"
    (event,) = enforced
    assert event.rule_kind == "header"
    assert HEADER_RULES_NOT_CONSULTED not in event.reasons
    assert not [r for r in event.reasons if region in r or header in r]


def test_a_sentinel_outside_the_glob_is_served() -> None:
    called, enforced = _decide("us-east-1", _sentinel("us-east-1"))

    assert called == [TOOL]
    assert enforced == []
