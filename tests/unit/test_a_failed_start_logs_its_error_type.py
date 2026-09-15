"""A failed start is logged by its error type, never by its message (#1454).

The message of a start failure can carry what the upstream wrote: its reply to
``initialize``, its stderr, anything it printed. Every path that starts a server
and logs the failure logs ``error_type`` instead -- the front-door warm-up, the
start itself and its handshake, a group starting its members, the rebalance
saga, and the saga manager that sends the recovery and failover sagas' starts.

Each test fails a start with a message that holds a sentinel, then asserts the
sentinel reaches no log line and the failure line names the error's type. The
caller still gets the full error; only the logs stop repeating it.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from mcp_hangar.application.commands import Command, StartMcpServerCommand, StopMcpServerCommand
from mcp_hangar.application.sagas.group_rebalance_saga import GroupRebalanceSaga
from mcp_hangar.domain.events import DomainEvent, McpServerDegraded
from mcp_hangar.domain.exceptions import McpServerStartError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.infrastructure.command_bus import CommandBus, CommandHandler
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.saga_manager import EventTriggeredSaga, Saga, SagaContext, SagaManager
from mcp_hangar.protocol import SESSION_TERMINATED_CODE, SESSION_TERMINATED_REASON
from mcp_hangar.server.lifecycle import warm_the_front_door_catalogue

SENTINEL = "SENTINEL-UPSTREAM-TEXT"
GET_LAUNCHER = "mcp_hangar.infrastructure.launchers.get_launcher"


def _start_error(mcp_server_id: str = "math") -> McpServerStartError:
    """What a start raises when the upstream answered with an error of its own."""
    return McpServerStartError(mcp_server_id=mcp_server_id, reason=f"MCP initialization failed: {SENTINEL}")


def _assert_logged_by_type(logs: list[dict[str, Any]], event: str, error_type: str) -> None:
    assert all(SENTINEL not in repr(entry) for entry in logs), logs
    lines = [entry for entry in logs if entry["event"] == event]
    assert lines, f"no {event!r} line in {logs}"
    for line in lines:
        assert line["error_type"] == error_type
        assert "error" not in line


def _server() -> McpServer:
    return McpServer(mcp_server_id="math", mode="subprocess", command=["unused"])


class TestTheFrontDoorWarmUp:
    @pytest.fixture(autouse=True)
    def _front_door(self):
        reset_tool_access_resolver()
        get_tool_access_resolver().set_topology_mode("front_door")
        yield
        reset_tool_access_resolver()

    def test_a_failed_start_is_logged_by_its_type(self) -> None:
        def send(command: StartMcpServerCommand) -> None:
            raise _start_error(command.mcp_server_id)

        runtime = SimpleNamespace(
            repository=SimpleNamespace(get_all_ids=lambda: ["math"], get=lambda _mcp_server_id: None),
            command_bus=SimpleNamespace(send=send),
        )

        with capture_logs() as logs:
            warm_the_front_door_catalogue(runtime)

        _assert_logged_by_type(logs, "front_door_warmup_failed", "McpServerStartError")


class TestTheStart:
    def test_a_failed_start_is_logged_by_its_type(self) -> None:
        client = MagicMock()
        client.process = None
        client.call.side_effect = ConnectionError(SENTINEL)
        launcher = MagicMock()
        launcher.launch.return_value = client
        server = _server()

        with capture_logs() as logs, patch(GET_LAUNCHER, return_value=launcher):
            with pytest.raises(McpServerStartError) as raised:
                server.ensure_ready()

        _assert_logged_by_type(logs, "mcp_server_start_failed", "ConnectionError")
        assert SENTINEL in str(raised.value), "the caller still gets the full error"

    def test_a_failed_initialized_notification_is_logged_by_its_type(self) -> None:
        client = MagicMock()
        client.process = None

        def _call(method: str, params: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
            if method == "initialize":
                return {"result": {"protocolVersion": "2025-06-18"}}
            return {"result": {"tools": []}}

        client.call.side_effect = _call
        client.notify.side_effect = RuntimeError(SENTINEL)

        with capture_logs() as logs:
            _server()._perform_mcp_handshake(client)

        _assert_logged_by_type(logs, "mcp_initialized_notification_failed", "RuntimeError")

    def test_a_failed_session_renegotiation_is_logged_by_its_type(self) -> None:
        terminated = {
            "error": {
                "code": SESSION_TERMINATED_CODE,
                "message": "Session terminated",
                "data": {"reason": SESSION_TERMINATED_REASON},
            }
        }
        client = MagicMock()
        client.call.return_value = terminated
        server = _server()

        with (
            capture_logs() as logs,
            patch.object(server, "_perform_mcp_handshake", side_effect=RuntimeError(SENTINEL)),
        ):
            assert server._call_with_session_recovery(client, "tools/call", {"name": "add"}) == terminated

        _assert_logged_by_type(logs, "mcp_session_renegotiation_failed", "RuntimeError")


class TestAGroup:
    def test_a_member_that_fails_to_start_is_logged_by_its_type(self) -> None:
        member = MagicMock()
        member.id = "math"
        member.mcp_server_id = "math"
        member.state = McpServerState.COLD
        member.ensure_ready.side_effect = _start_error()
        group = McpServerGroup(group_id="calculators", auto_start=False)
        group.add_member(member)

        with capture_logs() as logs:
            assert group.start_all() == 0

        _assert_logged_by_type(logs, "group_member_start_failed", "McpServerStartError")

    def test_the_rebalance_saga_logs_a_degraded_member_without_its_reason(self) -> None:
        # The line names the member, the group and the failure count, not the
        # reason: an event recorded by an earlier release carries the failed
        # start's message there (#1472 records the error type instead).
        saga = GroupRebalanceSaga(group_lookup=lambda _member_id: "calculators")

        with capture_logs() as logs:
            saga.handle(McpServerDegraded("math", 3, 3, f"Failed to start mcp_server: {SENTINEL}"))

        assert all(SENTINEL not in repr(entry) for entry in logs), logs
        assert [entry for entry in logs if entry["event"] == "group_member_degraded"] == [
            {
                "event": "group_member_degraded",
                "log_level": "info",
                "mcp_server_id": "math",
                "group_id": "calculators",
                "consecutive_failures": 3,
            }
        ]


class _FailingStart(CommandHandler):
    def handle(self, command: StartMcpServerCommand) -> None:
        raise _start_error(command.mcp_server_id)


class _Stop(CommandHandler):
    def handle(self, command: StopMcpServerCommand) -> dict[str, str]:
        return {"status": "ok"}


def _manager() -> tuple[SagaManager, EventBus]:
    command_bus = CommandBus()
    command_bus.register(StartMcpServerCommand, _FailingStart())
    command_bus.register(StopMcpServerCommand, _Stop())
    event_bus = EventBus()
    return SagaManager(command_bus=command_bus, event_bus=event_bus), event_bus


class _Failover(Saga):
    """Stops the primary, then starts a backup; undoing the stop starts the primary."""

    @property
    def saga_type(self) -> str:
        return "failover"

    def configure(self, context: SagaContext) -> None:
        self.add_step(
            name="stop_primary",
            command=StopMcpServerCommand(mcp_server_id="primary", reason="failover"),
            compensation_command=StartMcpServerCommand(mcp_server_id="primary"),
        )
        self.add_step(name="start_backup", command=StartMcpServerCommand(mcp_server_id="backup"))


class _RestartOnDegraded(EventTriggeredSaga):
    @property
    def saga_type(self) -> str:
        return "restart_on_degraded"

    @property
    def handled_events(self) -> list[type[DomainEvent]]:
        return [McpServerDegraded]

    def handle(self, event: DomainEvent) -> list[Command]:
        assert isinstance(event, McpServerDegraded)
        return [StartMcpServerCommand(mcp_server_id=event.mcp_server_id)]

    def to_dict(self) -> dict[str, Any]:
        return {}

    def from_dict(self, data: dict[str, Any]) -> None:
        pass


class TestTheSagaManager:
    def test_a_scheduled_start_and_its_follow_up_are_logged_by_their_type(self) -> None:
        # The recovery saga's restart: scheduled, and on failure followed up.
        manager, _ = _manager()

        with capture_logs() as logs:
            manager.schedule_command(
                StartMcpServerCommand(mcp_server_id="math"),
                delay_s=0,
                on_failure=lambda _error: [StartMcpServerCommand(mcp_server_id="math")],
            )
            deadline = time.monotonic() + 5
            while not any(entry["event"] == "scheduled_command_follow_up_failed" for entry in logs):
                assert time.monotonic() < deadline, logs
                time.sleep(0.01)

        _assert_logged_by_type(logs, "scheduled_command_failed", "McpServerStartError")
        _assert_logged_by_type(logs, "scheduled_command_follow_up_failed", "McpServerStartError")

    def test_a_failed_step_and_its_compensation_are_logged_by_their_type(self) -> None:
        manager, _ = _manager()

        with capture_logs() as logs:
            manager.start_saga(_Failover())

        _assert_logged_by_type(logs, "saga_step_failed", "McpServerStartError")
        _assert_logged_by_type(logs, "saga_compensation_failed", "McpServerStartError")

    def test_a_failed_command_from_an_event_saga_is_logged_by_its_type(self) -> None:
        manager, event_bus = _manager()
        manager.register_event_saga(_RestartOnDegraded())

        with capture_logs() as logs:
            event_bus.publish(McpServerDegraded("math", 1, 1, "start_failed"))

        _assert_logged_by_type(logs, "saga_command_failed", "McpServerStartError")
