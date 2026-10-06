"""A policy block that is not a mapping refuses the configuration (#1728).

#1648 refused a policy that did not parse and #1718 a list field that was not a
list, but each site only parsed a block that was a mapping and skipped any
other shape. `tools: add`, a `tool_access.member` tenant entry of `add` or
`access: {prompt: add}` booted that scope with no policy at all.

Every site now refuses a block that is present and is neither a mapping nor,
where one exists, its documented list form, so the boot fails and a reload is
refused with the previous policy kept in force.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from mcp_hangar.application.commands import ReloadConfigurationCommand
from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.server import config as server_config
from mcp_hangar.server.config import ServerConfigLoader, load_configuration
from mcp_hangar.server.state import GROUPS, get_runtime

pytestmark = pytest.mark.security

SERVER = "calc"
TENANT = "tenant:a"
IDS = (SERVER, "m1", "pool")


def _server(**extra: Any) -> dict[str, Any]:
    """A server that is never started: load only builds it."""
    return {"mode": "subprocess", "command": ["python", "-c", "pass"], **extra}


def _reset() -> None:
    reset_tool_access_resolver()
    server_config._BUILT_FROM.clear()
    repository = get_runtime().repository
    for mcp_server_id in IDS:
        if repository.exists(mcp_server_id):
            repository.remove(mcp_server_id)
    GROUPS.clear()


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield
    _reset()


def _servers(site: str, block: Any) -> dict[str, Any]:
    """A configuration that puts *block* at *site*."""
    if site == "server":
        return {SERVER: _server(tools=block)}
    if site == "tenant":
        return {SERVER: _server(tool_access={"member": {TENANT: block}})}
    if site == "tool_access.member":
        return {SERVER: _server(tool_access={"member": block})}
    if site == "tool_access":
        return {SERVER: _server(tool_access=block)}
    if site == "access.prompt":
        return {SERVER: _server(access={"prompt": block})}
    if site == "access":
        return {SERVER: _server(access=block)}
    if site == "group access":
        return {
            "m1": _server(),
            "pool": {"mode": "group", "auto_start": False, "access": block, "members": [{"id": "m1"}]},
        }
    if site == "group":
        return {
            "m1": _server(),
            "pool": {"mode": "group", "auto_start": False, "tools": block, "members": [{"id": "m1"}]},
        }
    if site == "member":
        return {
            "m1": _server(),
            "pool": {"mode": "group", "auto_start": False, "members": [{"id": "m1", "tools": block}]},
        }
    raise AssertionError(site)


#: Each site, and what the refusal must name to point at it.
SITES = {
    "server": f"tools access policy for mcp_server '{SERVER}'",
    "group": "tools access policy for group 'pool'",
    "member": "tools access policy for group 'pool' member 'm1'",
    "tenant": f"tools access policy on mcp_servers.{SERVER}.tool_access.member.{TENANT}",
    "tool_access.member": f"tool_access.member block on mcp_servers.{SERVER}",
    "tool_access": f"tool_access block on mcp_servers.{SERVER}",
    "access.prompt": f"access.prompt policy on mcp_servers.{SERVER}",
    "access": f"access block on mcp_servers.{SERVER}",
    "group access": "access block on mcp_servers.pool",
}

#: Shapes that are a mapping nowhere. YAML `tools:` with no value is None.
SCALARS = {"str": "add", "empty-str": "", "int": 1, "zero": 0, "bool": True, "false": False, "null": None}

#: Sites with no list form: only a server, or a member that defines its server inline, has tool schemas.
NO_LIST_FORM = [site for site in SITES if site != "server"]


class _Gateway:
    """A config file, booted the way bootstrap boots one, and the reload handler bootstrap wires."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.handler = ReloadConfigurationHandler(
            get_runtime().repository,
            _NullEventBus(),
            str(config_path),
            config_loader=ServerConfigLoader(),
            groups=GROUPS,
        )

    def _write(self, servers: dict[str, Any]) -> None:
        self.config_path.write_text(yaml.safe_dump({"mcp_servers": servers}, sort_keys=False))

    def boot(self, servers: dict[str, Any]) -> None:
        self._write(servers)
        load_configuration(str(self.config_path))

    def reload(self, servers: dict[str, Any]) -> None:
        self._write(servers)
        self.handler.handle(ReloadConfigurationCommand(requested_by="test"))


class _NullEventBus:
    def publish(self, event: Any) -> None:
        pass


@pytest.fixture
def gateway(tmp_path: Path) -> _Gateway:
    return _Gateway(tmp_path / "config.yaml")


class TestTheReproRefusesToBoot:
    def test_tools_add_as_a_string_refuses_the_boot(self, gateway: _Gateway) -> None:
        """On the old code this booted with no policy on the server."""
        with pytest.raises(ConfigurationError, match=rf"mcp_server '{SERVER}': expected a mapping .* got str 'add'"):
            gateway.boot({SERVER: _server(tools="add")})

        assert not get_runtime().repository.exists(SERVER)
        assert get_tool_access_resolver().iter_registered_policies() == []

    def test_a_list_of_tool_names_is_not_an_allow_list(self, gateway: _Gateway) -> None:
        """`tools: [add]` reads as an allow list; it is a list of tool schemas, and was an AttributeError."""
        with pytest.raises(ConfigurationError, match=rf"tools list of mcp_server '{SERVER}'.*got str 'add'"):
            gateway.boot({SERVER: _server(tools=["add"])})


class TestEverySiteRefusesABlockThatIsNotAMapping:
    @pytest.mark.parametrize("site", list(SITES))
    @pytest.mark.parametrize("value", list(SCALARS.values()), ids=list(SCALARS))
    def test_the_boot_is_refused_naming_the_scope(self, gateway: _Gateway, site: str, value: Any) -> None:
        with pytest.raises(ConfigurationError) as refused:
            gateway.boot(_servers(site, value))

        message = str(refused.value)
        assert f"Invalid {SITES[site]}: expected a mapping" in message
        assert f"got {type(value).__name__} {value!r}" in message
        assert get_tool_access_resolver().iter_registered_policies() == []

    @pytest.mark.parametrize("site", NO_LIST_FORM)
    @pytest.mark.parametrize("value", [["add"], []], ids=["list", "empty-list"])
    def test_a_list_is_refused_where_there_is_no_list_form(self, gateway: _Gateway, site: str, value: Any) -> None:
        with pytest.raises(ConfigurationError, match=rf"Invalid {SITES[site]}: expected a mapping, got list"):
            gateway.boot(_servers(site, value))


class TestTheValidShapesStillBoot:
    def test_a_server_with_tool_schemas_boots(self, gateway: _Gateway) -> None:
        gateway.boot({SERVER: _server(tools=[{"name": "add", "description": "Add"}])})

        assert get_runtime().repository.exists(SERVER)

    def test_an_inline_member_with_tool_schemas_boots(self, gateway: _Gateway) -> None:
        member = {"id": "m1", **_server(), "tools": [{"name": "add"}]}
        gateway.boot({"pool": {"mode": "group", "auto_start": False, "members": [member]}})

        assert get_runtime().repository.exists("m1")

    @pytest.mark.parametrize("site", list(SITES))
    def test_an_empty_mapping_is_no_policy(self, gateway: _Gateway, site: str) -> None:
        gateway.boot(_servers(site, {}))

        assert get_tool_access_resolver().iter_registered_policies() == []

    @pytest.mark.parametrize("site", ["server", "group", "member", "tenant", "access.prompt"])
    def test_a_policy_mapping_registers(self, gateway: _Gateway, site: str) -> None:
        gateway.boot(_servers(site, {"deny_list": ["add"]}))

        assert get_tool_access_resolver().iter_registered_policies() != []


class TestARefusedReloadKeepsThePreviousPolicy:
    @pytest.mark.parametrize("site", list(SITES))
    def test_add_is_still_denied_after_the_refused_reload(self, gateway: _Gateway, site: str) -> None:
        gateway.boot({SERVER: _server(tools={"deny_list": ["add"]})})
        before = get_tool_access_resolver().iter_registered_policies()

        servers = _servers(site, "add")
        servers.setdefault(SERVER, _server(tools={"deny_list": ["add"]}))
        with pytest.raises(ConfigurationError, match=rf"Invalid {SITES[site]}: expected a mapping"):
            gateway.reload(servers)

        assert get_tool_access_resolver().iter_registered_policies() == before
        assert not get_tool_access_resolver().is_tool_allowed(SERVER, "add")
