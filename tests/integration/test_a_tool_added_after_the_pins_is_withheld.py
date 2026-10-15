"""A tool that appears after the pins were taken is withheld until it is pinned (#1801).

`tool_projection.new_tools: withhold` on a server that has pins. The upstream
adds a tool after the baseline; the tool is not in `tools/list` and a call of it
is refused like a withdrawn tool, and one `ToolAdmissionRequested` says it is
waiting. Pinning it is the admission.

Both topologies run: the front door lists and calls the flat tools, `egress`
calls through `hangar_call`. The upstream and the catalogue refresh are real.

Naming: neutral placeholders only.
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import ToolAdmissionRequested
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.server.context import get_context

from ._front_door_harness import SERVER, TENANT_A, FrontDoor, front_door, jsonrpc

BASELINE = "read_item"
ADDED = "write_item"


def _pin_the_baseline(*, policy: str | None) -> None:
    registry = get_tool_projection_registry()
    (projection,) = [p for p in registry.list_for_server(SERVER) if p.tool == BASELINE]
    registry.set_config_pin(SERVER, BASELINE, None, ToolDigest(tool_name=BASELINE, sha256=projection.digest.sha256))
    if policy is not None:
        registry.set_new_tools_policy(SERVER, policy)


def _the_upstream_adds_a_tool(door: FrontDoor) -> None:
    door.upstream.tools = (BASELINE, ADDED)
    assert get_context().repository.get(SERVER).recheck_catalogue()


def _call_through_hangar_call(door: FrontDoor, tool: str) -> str | None:
    calls = [{"mcp_server": SERVER, "tool": tool, "arguments": {"x": "1"}}]
    payload = jsonrpc(door.call(TENANT_A, "hangar_call", {"calls": calls}))
    (result,) = payload["result"]["structuredContent"]["results"]
    return None if result["success"] else str(result["error_type"])


def _requests() -> list[ToolAdmissionRequested]:
    seen: list[ToolAdmissionRequested] = []
    get_context().runtime.event_bus.subscribe(ToolAdmissionRequested, seen.append, kind=HandlerKind.EFFECT)
    return seen


def test_the_front_door_does_not_list_or_serve_a_tool_added_after_the_pins() -> None:
    with front_door((BASELINE,)) as door:
        requested = _requests()
        _pin_the_baseline(policy="withhold")
        assert door.names(TENANT_A) == [BASELINE]

        _the_upstream_adds_a_tool(door)

        assert door.names(TENANT_A) == [BASELINE]
        assert door.upstream.called == []
        assert [(e.mcp_server_id, e.tool_name) for e in requested] == [(SERVER, ADDED)]


def test_hangar_call_refuses_it_and_admission_is_pinning_it() -> None:
    with front_door((BASELINE,), topology="egress") as door:
        requested = _requests()
        _pin_the_baseline(policy="withhold")
        _the_upstream_adds_a_tool(door)

        assert _call_through_hangar_call(door, BASELINE) is None
        assert _call_through_hangar_call(door, ADDED) == "ToolWithdrawnError"
        assert len(requested) == 1 and requested[0].observed_digest

        registry = get_tool_projection_registry()
        (projection,) = [p for p in registry.list_for_server(SERVER) if p.tool == ADDED]
        registry.set_config_pin(SERVER, ADDED, None, ToolDigest(tool_name=ADDED, sha256=projection.digest.sha256))

        assert _call_through_hangar_call(door, ADDED) is None


def test_it_asks_once_for_one_definition() -> None:
    with front_door((BASELINE,)) as door:
        requested = _requests()
        _pin_the_baseline(policy="withhold")
        _the_upstream_adds_a_tool(door)

        get_context().repository.get(SERVER).recheck_catalogue()
        door.names(TENANT_A)

        assert len(requested) == 1


@pytest.mark.parametrize("policy", [None, "allow"])
def test_without_withhold_the_new_tool_is_served_as_before(policy: str | None) -> None:
    with front_door((BASELINE,)) as door:
        requested = _requests()
        _pin_the_baseline(policy=policy)

        _the_upstream_adds_a_tool(door)

        assert door.names(TENANT_A) == [BASELINE, ADDED]
        assert requested == []


def test_a_server_with_no_pins_has_no_baseline_and_withholds_nothing() -> None:
    with front_door((BASELINE,)) as door:
        get_tool_projection_registry().set_new_tools_policy(SERVER, "withhold")

        _the_upstream_adds_a_tool(door)

        assert door.names(TENANT_A) == [BASELINE, ADDED]
