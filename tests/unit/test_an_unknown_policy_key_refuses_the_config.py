"""A key an access policy does not read refuses the configuration (#1773).

``tools: {allow: [read], deny: [add]}`` is the shape the README showed. The
parser reads ``allow_list``, ``deny_list`` and ``approval_list``, found none in
that block and returned no policy: the gateway booted, listed ``add`` and ran
it. A policy the operator wrote, not in force, with nothing saying so -- the
class of #1714, #1726 and #1740.

Any key of an access policy block that the parser does not read now refuses the
boot and the reload, naming the path and the key, at every site a policy is
written: a server, a group, a group member, ``access.<kind>`` and a per-tenant
entry. A refused reload keeps the previous policy, and ``mcp-hangar config
check`` reports the same keys.
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

#: The dotted path a refusal or `config check` names for each site.
PATHS = {
    "server": f"mcp_servers.{SERVER}.tools",
    "group": "mcp_servers.pool.tools",
    "member": "mcp_servers.pool.members.m1.tools",
    "tenant": f"mcp_servers.{SERVER}.tool_access.member.{TENANT}",
    "access": f"mcp_servers.{SERVER}.access.prompt",
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
    def test_allow_and_deny_refuse_the_boot_naming_the_path_and_the_fix(self, gateway: _Gateway) -> None:
        """On the old code this booted, listed `add` and executed it."""
        with pytest.raises(ConfigurationError) as refused:
            gateway.boot({SERVER: _server(tools={"allow": ["read"], "deny": ["add"]})})

        message = str(refused.value)
        assert f"mcp_servers.{SERVER}.tools.allow (did you mean allow_list?)" in message
        assert f"mcp_servers.{SERVER}.tools.deny (did you mean deny_list?)" in message
        assert not get_runtime().repository.exists(SERVER)
        assert get_tool_access_resolver().iter_registered_policies() == []

    def test_the_same_policy_with_the_right_keys_boots_and_denies(self, gateway: _Gateway) -> None:
        gateway.boot({SERVER: _server(tools={"allow_list": ["read"], "deny_list": ["add"]})})

        assert get_tool_access_resolver().is_tool_allowed(SERVER, "read")
        assert not get_tool_access_resolver().is_tool_allowed(SERVER, "add")

    def test_the_other_keys_of_a_policy_are_still_read(self, gateway: _Gateway) -> None:
        gateway.boot(
            {
                SERVER: _server(
                    tools={"approval_list": ["add"], "approval_timeout_seconds": 60, "approval_channel": "slack"}
                )
            }
        )

        assert get_tool_access_resolver().iter_registered_policies() != []

    def test_a_key_with_no_policy_beside_it_is_refused_too(self, gateway: _Gateway) -> None:
        """Not only the aliases: any key nothing reads, with or without lists next to it."""
        with pytest.raises(ConfigurationError, match=rf"mcp_servers\.{SERVER}\.tools\.denylist_typo"):
            gateway.boot({SERVER: _server(tools={"denylist_typo": ["add"]})})

    def test_an_empty_block_is_no_policy_and_boots(self, gateway: _Gateway) -> None:
        gateway.boot({SERVER: _server(tools={})})

        assert get_tool_access_resolver().iter_registered_policies() == []


class TestEverySiteRefusesAnUnknownKey:
    @pytest.mark.parametrize("site", list(SITES))
    @pytest.mark.parametrize("key", ["allow", "deny", "approve", "allowlist", "not_a_key"])
    def test_the_boot_is_refused_naming_the_scope_and_the_key(self, gateway: _Gateway, site: str, key: str) -> None:
        # `deny_list: [add]` beside it makes the policy non-empty, as a real one is.
        policy = {"deny_list": ["add"], key: ["read"]}

        with pytest.raises(ConfigurationError) as refused:
            gateway.boot(_servers(site, policy))

        message = str(refused.value)
        assert SITES[site] in message
        assert f"{PATHS[site]}.{key}" in message
        assert "not a key an access policy reads" in message
        assert get_tool_access_resolver().iter_registered_policies() == []

    def test_a_per_tenant_entry_may_carry_its_access_block(self, gateway: _Gateway) -> None:
        """`access` is read from that mapping by its own parser; it is not a stranger there."""
        servers = {
            SERVER: _server(
                tool_access={"member": {TENANT: {"deny_list": ["add"], "access": {"prompt": {"deny_list": ["x"]}}}}}
            )
        }

        gateway.boot(servers)

        assert get_tool_access_resolver().iter_registered_policies() != []


class TestARefusedReloadKeepsThePreviousPolicy:
    @pytest.mark.parametrize("site", list(SITES))
    def test_add_is_still_denied_after_the_refused_reload(self, gateway: _Gateway, site: str) -> None:
        gateway.boot(_servers(site, {"deny_list": ["add"]}))
        before = get_tool_access_resolver().iter_registered_policies()

        with pytest.raises(ConfigurationError, match="not a key an access policy reads"):
            gateway.reload(_servers(site, {"deny_list": ["add"], "allow": ["read"]}))

        assert get_tool_access_resolver().iter_registered_policies() == before


class TestConfigCheckReportsTheSameKeys:
    @pytest.mark.parametrize("site", list(SITES))
    def test_every_site_is_reported_with_its_path(self, site: str) -> None:
        from mcp_hangar.server.config_schema import validate_config

        problems = validate_config({"mcp_servers": _servers(site, {"deny_list": ["add"], "allow": ["read"]})})

        assert any(f"{PATHS[site]}.allow" in p and "did you mean allow_list?" in p for p in problems), problems

    def test_a_correct_policy_reports_nothing(self) -> None:
        from mcp_hangar.server.config_schema import validate_config

        for site in SITES:
            assert validate_config({"mcp_servers": _servers(site, {"deny_list": ["add"]})}) == []

    def test_the_cli_exits_non_zero_on_the_repro(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from mcp_hangar.server.cli import app

        path = tmp_path / "pol.yaml"
        path.write_text(yaml.safe_dump({"mcp_servers": {SERVER: _server(tools={"allow": ["read"], "deny": ["add"]})}}))

        result = CliRunner().invoke(app, ["config", "check", str(path)])

        assert result.exit_code == 1, result.output
        assert f"mcp_servers.{SERVER}.tools.allow" in result.output

    def test_the_schema_copy_of_the_keys_is_the_parsers(self) -> None:
        """The schema module imports nothing, so it holds a copy; this holds the copy to the original."""
        from mcp_hangar.domain.model import mcp_server_config
        from mcp_hangar.server import config as loader
        from mcp_hangar.server import config_schema

        assert frozenset(mcp_server_config._POLICY_KEYS) == config_schema.POLICY_KEYS
        assert tuple(loader._ACCESS_KINDS) == config_schema.ACCESS_KINDS
