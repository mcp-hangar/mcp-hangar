"""An invalid access policy refuses the configuration; it is never dropped (#1648).

Each site that parses a `tools:`-style policy -- a server, a group, a group
member, a per-tenant `tool_access.member` entry, and an `access:` block -- used
to catch the parser's `ValueError`, log a warning and carry on without the
policy. One bad field (`approval_timeout_seconds: 0`, an empty pattern, a
whitespace `approval_channel`) turned the whole policy off: the gateway booted
and a denied tool ran. Verified live: `deny_list: [add]` plus
`approval_timeout_seconds: 0` booted and `hangar_call` ran `add`.

Each site now refuses the file at load, naming the scope and the field, and a
refused reload leaves the previous policy in force.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import yaml

from mcp_hangar.application.commands import ReloadConfigurationCommand
from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler
from mcp_hangar.domain.events import ConfigurationReloadFailed
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.server import config as server_config
from mcp_hangar.server.config import ServerConfigLoader, load_configuration
from mcp_hangar.server.state import GROUPS, get_runtime

pytestmark = pytest.mark.security

SERVER = "calc"
TENANT = "tenant:a"
IDS = (SERVER, "m1", "pool")
#: The bad field the live repro used. Every case below adds it to a policy that is valid without it.
BAD = {"approval_timeout_seconds": 0}


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

    def __init__(self, path: Path) -> None:
        self.path = path
        self.events = Mock()
        self.handler = ReloadConfigurationHandler(
            get_runtime().repository, self.events, str(path), config_loader=ServerConfigLoader(), groups=GROUPS
        )

    def _write(self, servers: dict[str, Any]) -> None:
        self.path.write_text(yaml.safe_dump({"mcp_servers": servers}, sort_keys=False))

    def boot(self, servers: dict[str, Any]) -> None:
        self._write(servers)
        load_configuration(str(self.path))

    def reload(self, servers: dict[str, Any]) -> None:
        self._write(servers)
        self.handler.handle(ReloadConfigurationCommand(requested_by="test"))


@pytest.fixture
def gateway(tmp_path: Path) -> _Gateway:
    return _Gateway(tmp_path / "config.yaml")


class TestTheLiveReproRefusesToBoot:
    def test_a_deny_list_with_a_zero_approval_timeout_refuses_the_boot(self, gateway: _Gateway) -> None:
        with pytest.raises(ConfigurationError, match=rf"mcp_server '{SERVER}'.*approval_timeout_seconds: 0"):
            gateway.boot({SERVER: _server(tools={"deny_list": ["add"], **BAD})})

        assert not get_runtime().repository.exists(SERVER)
        assert get_tool_access_resolver().iter_registered_policies() == []

    def test_the_same_policy_without_the_bad_field_boots_and_denies(self, gateway: _Gateway) -> None:
        gateway.boot({SERVER: _server(tools={"deny_list": ["add"]})})

        assert not get_tool_access_resolver().is_tool_allowed(SERVER, "add")


class TestEverySiteRefusesAnInvalidPolicy:
    @pytest.mark.parametrize("site", list(SITES))
    @pytest.mark.parametrize(
        ("bad", "field"),
        [
            (BAD, "approval_timeout_seconds"),
            ({"deny_list": ["ok", ""]}, "deny_list"),
            ({"approval_channel": "   "}, "approval_channel"),
        ],
    )
    def test_the_boot_is_refused_naming_the_scope_and_the_field(
        self, gateway: _Gateway, site: str, bad: dict[str, Any], field: str
    ) -> None:
        policy = {"deny_list": ["add"], **bad}

        with pytest.raises(ConfigurationError) as refused:
            gateway.boot(_servers(site, policy))

        assert SITES[site] in str(refused.value)
        assert field in str(refused.value)
        assert get_tool_access_resolver().iter_registered_policies() == []

    @pytest.mark.parametrize("site", list(SITES))
    def test_a_policy_that_is_not_a_list_is_refused_naming_the_scope(self, gateway: _Gateway, site: str) -> None:
        with pytest.raises(ConfigurationError) as refused:
            gateway.boot(_servers(site, {"deny_list": 5}))

        assert SITES[site] in str(refused.value)


class TestARefusedReloadKeepsThePreviousPolicy:
    @pytest.mark.parametrize("site", list(SITES))
    def test_the_previous_policy_stays_in_force(
        self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch, site: str
    ) -> None:
        gateway.boot(_servers(site, {"deny_list": ["add"]}))
        resolver = get_tool_access_resolver()
        before = resolver.iter_registered_policies()
        assert ToolAccessPolicy(deny_list=("add",)) in [policy for _, policy in before]
        repository = get_runtime().repository
        running = {
            mcp_server_id: repository.get(mcp_server_id) for mcp_server_id in IDS if repository.exists(mcp_server_id)
        }
        shutdowns = {mcp_server_id: Mock() for mcp_server_id in running}
        for mcp_server_id, shutdown in shutdowns.items():
            monkeypatch.setattr(running[mcp_server_id], "shutdown", shutdown)

        with pytest.raises(ConfigurationError, match="approval_timeout_seconds"):
            gateway.reload(_servers(site, {"deny_list": ["add"], **BAD}))

        assert get_tool_access_resolver().iter_registered_policies() == before
        assert all(repository.get(mcp_server_id) is server for mcp_server_id, server in running.items())
        for shutdown in shutdowns.values():
            shutdown.assert_not_called()
        (failed,) = [
            c.args[0] for c in gateway.events.publish.call_args_list if isinstance(c.args[0], ConfigurationReloadFailed)
        ]
        assert failed.error_type == "ConfigurationError"

    def test_a_server_denied_add_still_denies_it_after_the_refused_reload(self, gateway: _Gateway) -> None:
        gateway.boot({SERVER: _server(tools={"deny_list": ["add"]})})

        with pytest.raises(ConfigurationError):
            gateway.reload({SERVER: _server(tools={"deny_list": ["add"], **BAD})})

        assert not get_tool_access_resolver().is_tool_allowed(SERVER, "add")
