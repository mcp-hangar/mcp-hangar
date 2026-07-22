"""Tests that McpServer.invoke_tool enforces an attached L7 egress policy.

The L7 check runs before ensure_ready, so a denied/approval-gated call raises
without starting the server or touching the upstream -- these tests need no
running process.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from mcp_hangar.domain.events import EgressPolicyViolationObserved
from mcp_hangar.domain.exceptions import EgressPolicyApprovalRequiredError, EgressPolicyDeniedError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import ArgumentRules, L7Policy, PolicyMode, ToolAction, ToolRules

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _server(policy: L7Policy | None) -> McpServer:
    return McpServer(mcp_server_id="s", mode="subprocess", command=["echo"], l7_policy=policy)


class _Proceeded(Exception):
    """Sentinel raised from a stubbed ensure_ready to prove the call fell through
    the L7 gate and proceeded (instead of being blocked)."""


def _observing_server(policy: L7Policy) -> McpServer:
    """A server whose ensure_ready raises _Proceeded, so a call that is NOT
    blocked by the L7 gate lands there -- letting us assert 'proceeded' without
    standing up a real upstream process."""
    server = _server(policy)
    server.ensure_ready = Mock(side_effect=_Proceeded())  # type: ignore[method-assign]
    return server


def _observed(server: McpServer) -> list[EgressPolicyViolationObserved]:
    return [e for e in server.collect_events() if isinstance(e, EgressPolicyViolationObserved)]


def test_denied_tool_raises() -> None:
    with pytest.raises(EgressPolicyDeniedError):
        _server(L7Policy(tools=ToolRules(deny=("delete_*",)))).invoke_tool("delete_repo", {})


def test_default_deny_raises() -> None:
    policy = L7Policy(tools=ToolRules(allow=("get_*",)), default_action=ToolAction.DENY)
    with pytest.raises(EgressPolicyDeniedError):
        _server(policy).invoke_tool("write_file", {})


def test_secret_in_arguments_denied() -> None:
    policy = L7Policy(tools=ToolRules(allow=("*",)), arguments=ArgumentRules(secret_patterns=("aws-keys",)))
    with pytest.raises(EgressPolicyDeniedError) as ei:
        _server(policy).invoke_tool("get_user", {"key": AWS_KEY})
    assert "aws-keys" in ei.value.reason


def test_require_approval_raises() -> None:
    with pytest.raises(EgressPolicyApprovalRequiredError):
        _server(L7Policy(tools=ToolRules(require_approval=("create_*",)))).invoke_tool("create_issue", {"title": "x"})


def test_no_policy_means_no_enforcement() -> None:
    server = McpServer(mcp_server_id="s", mode="subprocess", command=["echo"])
    assert server.l7_policy is None


def test_setter_attaches_and_enforces() -> None:
    server = McpServer(mcp_server_id="s", mode="subprocess", command=["echo"])
    assert server.l7_policy is None
    server.set_l7_policy(L7Policy(tools=ToolRules(deny=("*",))))
    with pytest.raises(EgressPolicyDeniedError):
        server.invoke_tool("anything", {})
    # Clearing disables enforcement again.
    server.set_l7_policy(None)
    assert server.l7_policy is None


# --- mode gating: Enforce blocks, Audit observes ---------------------------


def test_mode_absent_defaults_to_enforce_and_blocks() -> None:
    # A programmatically-built policy carries mode=Enforce by default -> blocks.
    policy = L7Policy(tools=ToolRules(deny=("delete_*",)))
    assert policy.mode is PolicyMode.ENFORCE
    with pytest.raises(EgressPolicyDeniedError):
        _server(policy).invoke_tool("delete_repo", {})


def test_enforce_mode_deny_raises() -> None:
    policy = L7Policy(tools=ToolRules(deny=("delete_*",)), mode=PolicyMode.ENFORCE)
    with pytest.raises(EgressPolicyDeniedError):
        _server(policy).invoke_tool("delete_repo", {})


def test_audit_mode_deny_observes_and_proceeds() -> None:
    policy = L7Policy(tools=ToolRules(deny=("delete_*",)), mode=PolicyMode.AUDIT)
    server = _observing_server(policy)
    # Not blocked: the call falls through the gate to ensure_ready (our sentinel).
    with pytest.raises(_Proceeded):
        server.invoke_tool("delete_repo", {})
    events = _observed(server)
    assert len(events) == 1
    assert events[0].would_be_action == ToolAction.DENY.value
    assert events[0].tool_name == "delete_repo"
    assert events[0].reasons  # non-empty audit reasons


def test_audit_mode_secret_in_args_observes_and_proceeds() -> None:
    policy = L7Policy(
        tools=ToolRules(allow=("*",)),
        arguments=ArgumentRules(secret_patterns=("aws-keys",)),
        mode=PolicyMode.AUDIT,
    )
    server = _observing_server(policy)
    with pytest.raises(_Proceeded):
        server.invoke_tool("get_user", {"key": AWS_KEY})
    events = _observed(server)
    assert len(events) == 1
    assert events[0].would_be_action == ToolAction.DENY.value


def test_audit_mode_require_approval_observes_and_proceeds() -> None:
    policy = L7Policy(tools=ToolRules(require_approval=("create_*",)), mode=PolicyMode.AUDIT)
    server = _observing_server(policy)
    with pytest.raises(_Proceeded):
        server.invoke_tool("create_issue", {"title": "x"})
    events = _observed(server)
    assert len(events) == 1
    assert events[0].would_be_action == ToolAction.REQUIRE_APPROVAL.value


def test_audit_mode_allowed_tool_is_not_observed() -> None:
    # A call the policy would ALLOW records no observation event.
    policy = L7Policy(tools=ToolRules(allow=("get_*",)), mode=PolicyMode.AUDIT)
    server = _observing_server(policy)
    with pytest.raises(_Proceeded):
        server.invoke_tool("get_user", {})
    assert _observed(server) == []
