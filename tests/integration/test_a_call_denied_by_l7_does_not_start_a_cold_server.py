"""A call an enforced L7 policy refuses does not start the cold server it names (#1708).

The batch executor started a COLD target before the aggregate evaluated the
policy, so a denied call logged ``mcp_server_started``, ran ``initialize`` and
``tools/list`` upstream, and left the server READY; the aggregate's own comment
promises a denied call "never cold-starts a server". Driven through
``hangar_call`` over the real streamable-HTTP transport (``_front_door_harness``):
the spare server is registered cold on a counting upstream.

Naming: neutral placeholders only (store, read_item).
"""

from __future__ import annotations

import json
from typing import Any

from mcp_hangar.domain.model.mcp_server import McpServerState
from mcp_hangar.domain.policies.egress_l7 import L7Policy, PolicyMode, ToolAction, ToolRules
from mcp_hangar.server.context import get_context
from tests.integration._front_door_harness import TENANT_A, FrontDoor, front_door, jsonrpc

TOOL = "read_item"
SPARE = "spare"


def _call_spare(door: FrontDoor) -> dict[str, Any]:
    response = door.call(TENANT_A, "hangar_call", {"calls": [{"mcp_server": SPARE, "tool": TOOL, "arguments": {}}]})
    result = jsonrpc(response)["result"]
    return dict(json.loads(result["content"][0]["text"]))


def _policy(action: ToolAction, mode: PolicyMode = PolicyMode.ENFORCE) -> L7Policy:
    return L7Policy(
        tools=ToolRules(deny=(TOOL,)) if action is ToolAction.DENY else ToolRules(),
        default_action=ToolAction.ALLOW,
        mode=mode,
    )


def _state() -> McpServerState:
    return get_context().repository.get(SPARE).state


def test_a_denied_call_leaves_the_server_cold_and_the_upstream_untouched() -> None:
    with front_door((TOOL,), topology="egress", also=(SPARE,)) as door:
        get_context().repository.get(SPARE).set_l7_policy(_policy(ToolAction.DENY))
        before = list(door.upstream.called)

        batch = _call_spare(door)

        assert _state() is McpServerState.COLD, "a denied call started the server"
        assert door.upstream.called == before, "a denied call reached its upstream"
        assert "EgressPolicyDeniedError" in json.dumps(batch), batch


def test_an_audit_policy_still_starts_the_server_and_serves_the_call() -> None:
    with front_door((TOOL,), topology="egress", also=(SPARE,)) as door:
        get_context().repository.get(SPARE).set_l7_policy(_policy(ToolAction.DENY, PolicyMode.AUDIT))

        _call_spare(door)

        assert _state() is McpServerState.READY, "audit mode observes and must not block the start"


def test_a_call_the_policy_allows_starts_the_server() -> None:
    with front_door((TOOL,), topology="egress", also=(SPARE,)) as door:
        get_context().repository.get(SPARE).set_l7_policy(_policy(ToolAction.ALLOW))

        _call_spare(door)

        assert _state() is McpServerState.READY


def test_an_approval_nobody_can_give_leaves_the_server_cold() -> None:
    """With no approval gate to ask, a requireApproval verdict is refused by the aggregate: same as a deny."""
    with front_door((TOOL,), topology="egress", also=(SPARE,)) as door:
        policy = L7Policy(tools=ToolRules(require_approval=(TOOL,)), default_action=ToolAction.ALLOW)
        get_context().repository.get(SPARE).set_l7_policy(policy)

        batch = _call_spare(door)

        assert _state() is McpServerState.COLD
        assert "EgressPolicyApprovalRequiredError" in json.dumps(batch), batch
