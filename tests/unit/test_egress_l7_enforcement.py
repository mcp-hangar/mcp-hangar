"""Tests that McpServer.invoke_tool enforces an attached L7 egress policy.

The L7 check runs before ensure_ready, so a denied/approval-gated call raises
without starting the server or touching the upstream -- these tests need no
running process.
"""

from __future__ import annotations

import pytest

from mcp_hangar.domain.exceptions import EgressPolicyApprovalRequiredError, EgressPolicyDeniedError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import ArgumentRules, L7Policy, ToolAction, ToolRules

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _server(policy: L7Policy | None) -> McpServer:
    return McpServer(mcp_server_id="s", mode="subprocess", command=["echo"], l7_policy=policy)


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
