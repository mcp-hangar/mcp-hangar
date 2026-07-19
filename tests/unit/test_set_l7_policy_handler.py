"""Tests for SetL7PolicyHandler -- the operator->core L7 policy transport sink."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.commands.crud_commands import SetL7PolicyCommand
from mcp_hangar.application.commands.crud_handlers import SetL7PolicyHandler
from mcp_hangar.domain.exceptions import EgressPolicyDeniedError, McpServerNotFoundError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import L7Policy, ToolRules
from mcp_hangar.domain.repository import InMemoryMcpServerRepository


def _setup() -> tuple[SetL7PolicyHandler, InMemoryMcpServerRepository, McpServer]:
    repo = InMemoryMcpServerRepository()
    server = McpServer(mcp_server_id="p", mode="subprocess", command=["x"])
    repo.add("p", server)
    return SetL7PolicyHandler(repository=repo, event_bus=MagicMock()), repo, server


def test_set_attaches_policy() -> None:
    handler, _, server = _setup()
    policy = L7Policy(tools=ToolRules(deny=("delete_*",)))

    result = handler.handle(SetL7PolicyCommand(mcp_server_id="p", policy=policy, source="operator"))

    assert result == {"mcp_server_id": "p", "l7_policy_set": True}
    assert server.l7_policy is policy


def test_clear_removes_policy() -> None:
    handler, _, server = _setup()
    server.set_l7_policy(L7Policy(tools=ToolRules(deny=("*",))))

    result = handler.handle(SetL7PolicyCommand(mcp_server_id="p", policy=None, source="operator"))

    assert result == {"mcp_server_id": "p", "l7_policy_set": False}
    assert server.l7_policy is None


def test_unknown_server_raises() -> None:
    handler, _, _ = _setup()
    with pytest.raises(McpServerNotFoundError):
        handler.handle(SetL7PolicyCommand(mcp_server_id="nope", policy=None))


def test_set_then_invoke_enforces_end_to_end() -> None:
    # The transport sink and the invoke-path enforcement meet: a policy set via
    # the handler is enforced by the same aggregate's invoke_tool.
    handler, _, server = _setup()
    handler.handle(SetL7PolicyCommand(mcp_server_id="p", policy=L7Policy(tools=ToolRules(deny=("delete_*",)))))
    with pytest.raises(EgressPolicyDeniedError):
        server.invoke_tool("delete_repo", {})
