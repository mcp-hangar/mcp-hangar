"""A server the file removed leaves no stored tool-access policy for the next one under its id (#2224).

A reload that removed a server cleared its policies from the resolver and left
the rows the REST endpoint had stored for it. The startup replay applies every
row by scope and id, so after a restart the removed server's policy was back,
and a server created later under the same id was governed by it -- the
inheritance #1028 rules out, on the restart path only.

Each test here crosses one or more restarts on one SQLite policy store, through
the real `ReloadConfigurationHandler`, `ServerConfigLoader`,
`SetToolAccessPolicyHandler` and `_replay_tap_policies`. A restart is what
bootstrap does in that order: the file, the fleet the store restores, then the
replay.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from mcp_hangar.application.commands import ReloadConfigurationCommand
from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler
from mcp_hangar.auth.bootstrap import _replay_tap_policies
from mcp_hangar.auth.commands.commands import SetToolAccessPolicyCommand
from mcp_hangar.auth.commands.handlers import SetToolAccessPolicyHandler
from mcp_hangar.auth.infrastructure.sqlite_tap_store import SQLiteToolAccessPolicyStore
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.server import config as server_config
from mcp_hangar.server.config import ServerConfigLoader, load_configuration
from mcp_hangar.server.context import get_context
from mcp_hangar.server.state import GROUPS, get_runtime

DROPPED = "dropped"
KEPT = "kept"
API = "made-over-api"
IDS = (DROPPED, KEPT, API)
NEVER_STARTED = ["python", "-c", "pass"]


def _server(**extra: object) -> dict[str, object]:
    return {"mode": "subprocess", "command": NEVER_STARTED, **extra}


def _clear_process() -> None:
    reset_tool_access_resolver()
    server_config._BUILT_FROM.clear()
    repository = get_runtime().repository
    for mcp_server_id in IDS:
        if repository.exists(mcp_server_id):
            repository.remove(mcp_server_id)
    GROUPS.clear()


class _Gateway:
    """One config file and one policy database, booted, reloaded and restarted."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._db = tmp_path / "auth.db"
        self._path = tmp_path / "config.yaml"
        self._monkeypatch = monkeypatch
        self.store: SQLiteToolAccessPolicyStore | None = None

    def start(self, servers: dict[str, object], *, made_over_api: tuple[str, ...] = ()) -> None:
        """A process start: the file, the servers the store restores, then the stored policies."""
        self.stop()
        _clear_process()
        self.store = SQLiteToolAccessPolicyStore(self._db)
        self._monkeypatch.setattr(get_context(), "auth_components", SimpleNamespace(tap_store=self.store))
        self._write(servers)
        load_configuration(str(self._path))
        for mcp_server_id in made_over_api:
            self.create_over_api(mcp_server_id)
        _replay_tap_policies(self.store)

    def stop(self) -> None:
        if self.store is not None:
            self.store.close()
            self.store = None

    def create_over_api(self, mcp_server_id: str) -> None:
        server = McpServer(mcp_server_id=mcp_server_id, mode="subprocess", command=NEVER_STARTED)
        get_runtime().repository.add(mcp_server_id, server)

    def deny_over_rest(self, mcp_server_id: str, *tools: str) -> None:
        SetToolAccessPolicyHandler(tap_store=self.store, event_bus=Mock()).handle(
            SetToolAccessPolicyCommand(scope="provider", target_id=mcp_server_id, allow_list=[], deny_list=list(tools))
        )

    def reload(self, servers: dict[str, object]) -> dict[str, object]:
        self._write(servers)
        handler = ReloadConfigurationHandler(
            get_runtime().repository, Mock(), str(self._path), config_loader=ServerConfigLoader(), groups=GROUPS
        )
        return handler.handle(ReloadConfigurationCommand(requested_by="test"))

    def _write(self, servers: dict[str, object]) -> None:
        self._path.write_text(yaml.safe_dump({"mcp_servers": servers}, sort_keys=False))


def _policy(mcp_server_id: str) -> ToolAccessPolicy | None:
    return get_tool_access_resolver().get_configured_policy("provider", mcp_server_id)


@pytest.fixture
def gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _clear_process()
    gw = _Gateway(tmp_path, monkeypatch)
    yield gw
    gw.stop()
    _clear_process()


class TestAServerTheFileRemoved:
    def test_a_server_created_again_after_a_restart_has_none_of_its_policy(self, gateway: _Gateway) -> None:
        """The reported sequence: removed by a reload, restarted, created again over REST."""
        gateway.start({DROPPED: _server(), KEPT: _server()})
        gateway.deny_over_rest(DROPPED, "add")
        assert _policy(DROPPED) == ToolAccessPolicy(deny_list=("add",))

        result = gateway.reload({KEPT: _server()})

        assert result["mcp_servers_removed"] == [DROPPED]
        assert gateway.store.get_policy("provider", DROPPED) is None, "the stored row outlived the server"
        assert _policy(DROPPED) is None

        gateway.start({KEPT: _server()})
        assert _policy(DROPPED) is None, "the restart replayed the removed server's policy"

        gateway.create_over_api(DROPPED)
        assert _policy(DROPPED) is None

        # And a second restart, with the new server restored from the store.
        gateway.start({KEPT: _server()}, made_over_api=(DROPPED,))
        assert _policy(DROPPED) is None

    def test_a_reload_after_the_new_server_is_created_does_not_hand_it_the_policy(self, gateway: _Gateway) -> None:
        """A reload replays the store as a restart does, so it must find no row either."""
        gateway.start({DROPPED: _server(), KEPT: _server()})
        gateway.deny_over_rest(DROPPED, "add")
        gateway.reload({KEPT: _server()})
        gateway.create_over_api(DROPPED)

        gateway.reload({KEPT: _server()})

        assert _policy(DROPPED) is None

    def test_a_store_peers_may_share_keeps_the_row(self, gateway: _Gateway, monkeypatch: pytest.MonkeyPatch) -> None:
        """A peer whose file still declares the server replays that row on its own restart."""
        gateway.start({DROPPED: _server(), KEPT: _server()})
        gateway.deny_over_rest(DROPPED, "add")
        coordination = importlib.import_module("mcp_hangar.server.bootstrap.coordination")
        monkeypatch.setattr(coordination, "get_lease_keeper", lambda: object())

        gateway.reload({KEPT: _server()})

        assert gateway.store.get_policy("provider", DROPPED) == ToolAccessPolicy(deny_list=("add",))


class TestWhatStillExistsKeepsItsPolicy:
    def test_a_declared_server_keeps_its_runtime_policy_and_gate_across_a_restart(self, gateway: _Gateway) -> None:
        """#915 must not regress, even when the same reload removes another server."""
        gated = _server(tools={"approval_list": ["refund_*"]})
        gateway.start({DROPPED: _server(), KEPT: gated})
        gateway.deny_over_rest(KEPT, "internal_*")
        gateway.deny_over_rest(DROPPED, "add")

        gateway.reload({KEPT: gated})
        gateway.start({KEPT: gated})

        after = _policy(KEPT)
        assert after is not None
        assert after.deny_list == ("internal_*",)
        assert after.requires_approval("refund_payment"), "the restart dropped the file's consent gate"

    def test_a_rest_policy_on_a_server_made_over_the_api_survives_a_reload_and_a_restart(
        self, gateway: _Gateway
    ) -> None:
        """The file does not own it, so a reload removes neither it nor its row (#2169)."""
        gateway.start({DROPPED: _server()})
        gateway.create_over_api(API)
        gateway.deny_over_rest(API, "add")

        result = gateway.reload({})

        assert result["mcp_servers_removed"] == [DROPPED]
        gateway.start({}, made_over_api=(API,))
        assert _policy(API) == ToolAccessPolicy(deny_list=("add",))
