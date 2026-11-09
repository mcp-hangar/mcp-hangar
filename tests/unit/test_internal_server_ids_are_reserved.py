"""No server can take an id Hangar uses internally (#2204).

The tool access resolver keeps the gateway-wide policy floor under the server
id `_global`, in the same table as every server's own policy. A server
registered under that id would read and write the floor: a hot-load with a tool
list would replace it, and unloading the server would remove it.

`McpServerId` refuses a reserved id, and each door that registers a server
refuses it first, with an error that names it: the configuration file at
startup, a reload, and `hangar_load`.
"""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_hangar.application.commands.commands import LoadMcpServerCommand
from mcp_hangar.application.commands.load_handlers import LoadMcpServerHandler
from mcp_hangar.application.read_models.tool_projection import ToolProjectionRegistry
from mcp_hangar.domain.contracts.registry import ServerDetails
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.services import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.services.access_decision import decide
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver
from mcp_hangar.domain.value_objects import (
    GLOBAL_POLICY_SCOPE_ID,
    RESERVED_MCP_SERVER_IDS,
    McpServerId,
    ToolAccessPolicy,
)
from mcp_hangar.domain.value_objects.access import Caller, Target
from mcp_hangar.server import config as server_config

_SUBPROCESS = {"mode": "subprocess", "command": ["python", "-m", "example_server"]}


@pytest.fixture(autouse=True)
def fresh_resolver():
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


@pytest.fixture
def no_store(monkeypatch):
    """A runtime with no configuration store, so nothing but the file decides."""
    monkeypatch.setattr(server_config, "get_runtime", lambda: SimpleNamespace(config_repository=None))


class TestTheReservedSet:
    def test_it_is_the_key_the_resolver_keeps_the_floor_under(self) -> None:
        """Pins the constant to the resolver: a policy registered under it applies to every server."""
        resolver = ToolAccessResolver()
        resolver.set_mcp_server_policy(GLOBAL_POLICY_SCOPE_ID, ToolAccessPolicy(deny_list=("delete_*",)))

        target, overlay = Target.server("unrelated-server"), ToolProjectionRegistry()

        assert not decide(target, "delete_rows", Caller(), resolver=resolver, withdrawals=overlay).allowed
        assert decide(target, "get_rows", Caller(), resolver=resolver, withdrawals=overlay).allowed
        assert GLOBAL_POLICY_SCOPE_ID in RESERVED_MCP_SERVER_IDS

    @pytest.mark.parametrize("reserved", sorted(RESERVED_MCP_SERVER_IDS))
    def test_the_value_object_refuses_it(self, reserved: str) -> None:
        with pytest.raises(ValueError, match="reserved"):
            McpServerId(reserved)

    def test_a_similar_id_is_still_a_server_id(self) -> None:
        assert McpServerId("global").value == "global"


class TestTheConfigurationFile:
    def test_startup_refuses_a_server_under_a_reserved_id(self, no_store) -> None:
        with pytest.raises(ConfigurationError, match="'_global' is reserved"):
            server_config.load_config({GLOBAL_POLICY_SCOPE_ID: _SUBPROCESS})

    def test_a_reload_refuses_it_before_anything_is_stopped(self, no_store) -> None:
        with pytest.raises(ConfigurationError, match="'_global' is reserved"):
            server_config.ServerConfigLoader().prepare_mcp_servers(
                {"kept": _SUBPROCESS, GLOBAL_POLICY_SCOPE_ID: _SUBPROCESS}
            )

    def test_a_group_member_under_a_reserved_id_is_refused(self, no_store) -> None:
        group = {"mode": "group", "members": [{"id": GLOBAL_POLICY_SCOPE_ID, **_SUBPROCESS}]}

        with pytest.raises(ConfigurationError, match="'_global' is reserved"):
            server_config.load_config({"pool": group})

    def test_a_member_id_that_is_not_a_string_is_still_only_skipped(self, no_store) -> None:
        group = {"mode": "group", "members": [{"id": ["a", "b"], **_SUBPROCESS}]}

        server_config.build_config({"pool": group})


class _Bus:
    def publish(self, event: Any) -> None:
        pass


def _registry_entry(server_id: str) -> ServerDetails:
    return ServerDetails(
        id=server_id,
        name="example",
        description="",
        vendor=None,
        source_url=None,
        is_published=True,
        packages=[],
        required_env_vars=[],
    )


class TestHangarLoad:
    def _handler(self, server_id: str) -> tuple[LoadMcpServerHandler, MagicMock, MagicMock]:
        repository = MagicMock()
        repository.exists.return_value = False
        store = MagicMock()
        store.exists.return_value = False
        secrets = MagicMock()
        factory = MagicMock()
        handler = LoadMcpServerHandler(
            registry_client=MagicMock(),
            package_resolver=MagicMock(),
            secrets_resolver=secrets,
            installers=[],
            runtime_store=store,
            event_bus=_Bus(),
            mcp_server_factory=factory,
            mcp_server_repository=repository,
        )
        handler._find_server = AsyncMock(return_value=_registry_entry(server_id))  # type: ignore[method-assign]
        return handler, secrets, factory

    @pytest.mark.parametrize("registry_id", [GLOBAL_POLICY_SCOPE_ID, "._global", "/_global/"])
    def test_a_registry_entry_that_sanitizes_to_a_reserved_id_is_refused(self, registry_id: str) -> None:
        floor = ToolAccessPolicy(deny_list=("delete_*",))
        get_tool_access_resolver().set_mcp_server_policy(GLOBAL_POLICY_SCOPE_ID, floor)
        handler, secrets, factory = self._handler(registry_id)

        result = asyncio.run(handler.handle(LoadMcpServerCommand(name="example", allow_tools=["*"])))

        assert result.status == "failed"
        assert "'_global' is reserved" in result.message
        secrets.resolve.assert_not_called()
        factory.assert_not_called()
        assert get_tool_access_resolver().get_configured_policy("mcp_server", GLOBAL_POLICY_SCOPE_ID) == floor

    def test_a_reserved_name_is_refused_whatever_the_registry_answers(self) -> None:
        handler, secrets, factory = self._handler("io.github.example/server")

        result = asyncio.run(handler.handle(LoadMcpServerCommand(name=GLOBAL_POLICY_SCOPE_ID)))

        assert result.status == "failed"
        assert "'_global' is reserved" in result.message
        secrets.resolve.assert_not_called()
        factory.assert_not_called()
