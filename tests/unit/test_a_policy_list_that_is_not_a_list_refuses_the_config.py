"""A policy list given as a string or a mapping refuses the configuration (#1718).

`ToolsConfig` only checked each item of `allow_list` / `deny_list` /
`approval_list`, never that the field was a list. A string iterates as its
characters, so `deny_list: add` became the patterns `a`, `d`, `d`: each a valid
non-empty string, the gateway booted, and `add` was allowed. A mapping
iterated as its keys.

The parser now refuses it, and every config site turns that into a
`ConfigurationError` (#1648), so the boot fails and a reload is refused with
the previous policy kept in force.
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
FIELDS = ("allow_list", "deny_list", "approval_list")


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


def _servers(site: str, policy: dict[str, Any]) -> dict[str, Any]:
    """A configuration that puts *policy* at *site*."""
    if site == "server":
        return {SERVER: _server(tools=policy)}
    if site == "tenant":
        return {SERVER: _server(tool_access={"member": {TENANT: policy}})}
    if site == "access":
        return {SERVER: _server(access={"prompt": policy})}
    if site == "group":
        return {
            "m1": _server(),
            "pool": {"mode": "group", "auto_start": False, "tools": policy, "members": [{"id": "m1"}]},
        }
    if site == "member":
        return {
            "m1": _server(),
            "pool": {"mode": "group", "auto_start": False, "members": [{"id": "m1", "tools": policy}]},
        }
    raise AssertionError(site)


#: Each site, and what the refusal must name to point at it.
SITES = {
    "server": f"mcp_server '{SERVER}'",
    "group": "group 'pool'",
    "member": "group 'pool' member 'm1'",
    "tenant": f"mcp_servers.{SERVER}.tool_access.member.{TENANT}",
    "access": f"access.prompt policy on mcp_servers.{SERVER}",
}


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
    def test_deny_list_add_as_a_string_refuses_the_boot(self, gateway: _Gateway) -> None:
        """On the old code this booted and `add` was allowed."""
        with pytest.raises(ConfigurationError, match=rf"mcp_server '{SERVER}'.*deny_list: expected a list"):
            gateway.boot({SERVER: _server(tools={"deny_list": "add"})})

        assert not get_runtime().repository.exists(SERVER)
        assert get_tool_access_resolver().iter_registered_policies() == []

    def test_the_same_policy_written_as_a_list_boots_and_denies(self, gateway: _Gateway) -> None:
        gateway.boot({SERVER: _server(tools={"deny_list": ["add"]})})

        assert not get_tool_access_resolver().is_tool_allowed(SERVER, "add")


class TestEverySiteRefusesAListThatIsNotAList:
    @pytest.mark.parametrize("site", list(SITES))
    @pytest.mark.parametrize("field", FIELDS)
    @pytest.mark.parametrize("value", ["add", {"add": None}, ""], ids=["str", "mapping", "empty-str"])
    def test_the_boot_is_refused_naming_the_scope_and_the_field(
        self, gateway: _Gateway, site: str, field: str, value: Any
    ) -> None:
        # `deny_list: [add]` beside it makes the policy non-empty whatever the field under test.
        policy = {"deny_list": ["add"], field: value}

        with pytest.raises(ConfigurationError) as refused:
            gateway.boot(_servers(site, policy))

        message = str(refused.value)
        assert SITES[site] in message
        # The type check, not some other refusal (an `access` block refuses any approval_list).
        assert f"Invalid {field}: expected a list of patterns" in message
        assert get_tool_access_resolver().iter_registered_policies() == []


class TestARefusedReloadKeepsThePreviousPolicy:
    @pytest.mark.parametrize("field", FIELDS)
    def test_add_is_still_denied_after_the_refused_reload(self, gateway: _Gateway, field: str) -> None:
        gateway.boot({SERVER: _server(tools={"deny_list": ["add"]})})
        before = get_tool_access_resolver().iter_registered_policies()

        with pytest.raises(ConfigurationError, match=rf"Invalid {field}: expected a list of patterns"):
            gateway.reload({SERVER: _server(tools={"deny_list": ["add"], field: "add"})})

        assert get_tool_access_resolver().iter_registered_policies() == before
        assert not get_tool_access_resolver().is_tool_allowed(SERVER, "add")
