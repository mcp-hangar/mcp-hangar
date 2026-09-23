"""Tests for SetL7PolicyHandler -- the operator->core L7 policy transport sink."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

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

    assert result == {"mcp_server_id": "p", "l7_policy_set": True, "persisted": False}
    assert server.l7_policy is policy


def test_clear_removes_policy() -> None:
    handler, _, server = _setup()
    server.set_l7_policy(L7Policy(tools=ToolRules(deny=("*",))))

    result = handler.handle(SetL7PolicyCommand(mcp_server_id="p", policy=None, source="operator"))

    assert result == {"mcp_server_id": "p", "l7_policy_set": False, "persisted": False}
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


class TestThePushSaysWhetherARestartKeepsThePolicy:
    """The operator re-delivers only on its next reconcile (#1306).

    A policy the gateway does not keep leaves every restart ungoverned until
    then, while the CR still reports it. The push is the one moment the gateway
    and the operator talk, so that is where it has to say so.
    """

    @staticmethod
    def _push(fleet_writer: object | None, restore_gap: str | None) -> tuple[dict, list[dict]]:
        repo = InMemoryMcpServerRepository()
        repo.add("p", McpServer(mcp_server_id="p", mode="subprocess", command=["x"]))
        handler = SetL7PolicyHandler(
            repository=repo, event_bus=MagicMock(), fleet_writer=fleet_writer, restore_gap=restore_gap
        )
        with capture_logs() as logs:
            result = handler.handle(SetL7PolicyCommand(mcp_server_id="p", policy=L7Policy(), source="operator"))
        return result, [e for e in logs if e["event"] == "l7_policy_not_persisted"]

    def test_a_policy_recorded_and_read_back_at_start_is_persisted(self) -> None:
        result, warnings = self._push(MagicMock(), None)

        assert result["persisted"] is True
        assert warnings == []

    @pytest.mark.parametrize(
        ("fleet_writer", "restore_gap", "reason"),
        [
            # The chart default: nothing is written anywhere.
            (None, None, "no_durable_backend"),
            # Written, but MCP_AUTO_RECOVER=false: nothing reads it back.
            (MagicMock(), "auto_recover_off", "auto_recover_off"),
        ],
    )
    def test_a_policy_a_restart_drops_is_not_persisted_and_warns(
        self, fleet_writer: object | None, restore_gap: str | None, reason: str
    ) -> None:
        result, warnings = self._push(fleet_writer, restore_gap)

        assert result["persisted"] is False
        assert [(w["log_level"], w["reason"], w["mcp_server_id"]) for w in warnings] == [("warning", reason, "p")]
