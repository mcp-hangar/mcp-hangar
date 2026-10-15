"""A server the configuration declares is refused by the delete command, not removed until the next reload (#1744).

`DELETE /api/mcp_servers/{id}` used to stop and remove any server. One that the
gateway's configuration declares came back on the next reload or restart, so
the delete said it worked and then undid itself; meanwhile the server was out
of the pool. The Kubernetes operator sent that delete for every MCPServer CR it
deleted, and a remote CR mirrors a declared server, so deleting the mirror
deleted the real one.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.commands.crud_commands import DeleteMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import DeleteMcpServerHandler
from mcp_hangar.application.read_models.tool_projection import reset_tool_projection_registry
from mcp_hangar.domain.exceptions import McpServerDeclaredInConfigError
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.server import config as server_config
from mcp_hangar.server.config import declared_in_config, load_config
from mcp_hangar.server.state import get_runtime

DECLARED = "declared"
OTHER = "other"
IDS = (DECLARED, OTHER)
SPEC = {"mode": "subprocess", "command": ["python", "-m", "some.server"]}


def _reset() -> None:
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    server_config._BUILT_FROM.clear()
    repository = get_runtime().repository
    for mcp_server_id in IDS:
        if repository.exists(mcp_server_id):
            repository.remove(mcp_server_id)


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield
    _reset()


def _ready_server(mcp_server_id: str) -> MagicMock:
    server = MagicMock(spec=McpServer)
    server.state = McpServerState.READY
    server.id = mcp_server_id
    return server


class TestTheDeleteHandler:
    def test_refuses_a_declared_server_and_leaves_it_running(self) -> None:
        repository = InMemoryMcpServerRepository()
        server = _ready_server(DECLARED)
        repository.add(DECLARED, server)
        fleet_writer = MagicMock()
        event_bus = MagicMock()
        handler = DeleteMcpServerHandler(
            repository=repository,
            event_bus=event_bus,
            fleet_writer=fleet_writer,
            declared_in_config=lambda mcp_server_id: mcp_server_id == DECLARED,
        )

        with pytest.raises(McpServerDeclaredInConfigError, match="declared in the gateway configuration"):
            handler.handle(DeleteMcpServerCommand(mcp_server_id=DECLARED))

        assert repository.exists(DECLARED)
        server.shutdown.assert_not_called()
        fleet_writer.delete.assert_not_called()
        event_bus.publish.assert_not_called()

    def test_deletes_a_server_the_configuration_does_not_declare(self) -> None:
        repository = InMemoryMcpServerRepository()
        repository.add(OTHER, _ready_server(OTHER))
        handler = DeleteMcpServerHandler(
            repository=repository,
            event_bus=MagicMock(),
            declared_in_config=lambda mcp_server_id: mcp_server_id == DECLARED,
        )

        handler.handle(DeleteMcpServerCommand(mcp_server_id=OTHER))

        assert not repository.exists(OTHER)


class TestDeclaredInConfig:
    def test_is_true_for_a_server_the_configuration_built(self) -> None:
        load_config({DECLARED: dict(SPEC)})

        assert declared_in_config(DECLARED)

    def test_is_false_for_a_server_added_at_runtime(self) -> None:
        load_config({DECLARED: dict(SPEC)})
        get_runtime().repository.add(OTHER, McpServer(mcp_server_id=OTHER, **SPEC))

        assert not declared_in_config(OTHER)

    def test_is_false_once_a_reload_drops_the_server(self) -> None:
        load_config({DECLARED: dict(SPEC)})
        load_config({OTHER: dict(SPEC)}, replace=True)

        assert not declared_in_config(DECLARED)
        assert declared_in_config(OTHER)

    def test_the_wired_handler_refuses_what_the_loaded_configuration_declares(self) -> None:
        load_config({DECLARED: dict(SPEC)})
        handler = DeleteMcpServerHandler(
            repository=get_runtime().repository,
            event_bus=MagicMock(),
            declared_in_config=declared_in_config,
        )

        with pytest.raises(McpServerDeclaredInConfigError):
            handler.handle(DeleteMcpServerCommand(mcp_server_id=DECLARED))

        assert get_runtime().repository.exists(DECLARED)


class TestTheRoute:
    @pytest.fixture
    def api_client(self):
        from mcp_hangar.server.api import create_api_router

        ctx = Mock()
        ctx.command_bus.send.side_effect = McpServerDeclaredInConfigError(DECLARED)
        with patch("mcp_hangar.server.api.middleware.get_context", return_value=ctx):
            yield TestClient(create_api_router(), raise_server_exceptions=False)

    def test_answers_409_naming_the_reason(self, api_client) -> None:
        response = api_client.delete(f"/mcp_servers/{DECLARED}")

        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "declared_in_config"
        assert body["mcp_server_id"] == DECLARED
        assert "remove it from the configuration and reload" in body["detail"]
