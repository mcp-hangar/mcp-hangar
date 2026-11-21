"""A server deleted over REST or unloaded leaves no tool-access policy for the next one under its id (#2298).

Only a reload cleared a removed server's policies from both the resolver and the
store (#1028, #2224). The REST delete cleared neither, so a server created again
under the id was governed by its predecessor's policy at once. `hangar_unload`
cleared the resolver and left the stored row, which the next start replayed.

Through the real `DeleteMcpServerHandler`, `CreateMcpServerHandler`,
`UnloadMcpServerHandler`, `StoredServerPolicies`, `SetToolAccessPolicyHandler`
and `_replay_tap_policies` on one SQLite store. A restart is the replay onto a
fresh resolver.
"""

from __future__ import annotations

import importlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.application.commands import UnloadMcpServerCommand
from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand, DeleteMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler, DeleteMcpServerHandler
from mcp_hangar.application.commands.load_handlers import UnloadMcpServerHandler
from mcp_hangar.auth.bootstrap import _replay_tap_policies
from mcp_hangar.auth.commands.commands import SetToolAccessPolicyCommand
from mcp_hangar.auth.commands.handlers import SetToolAccessPolicyHandler
from mcp_hangar.auth.infrastructure.sqlite_tap_store import SQLiteToolAccessPolicyStore
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.domain.value_objects.provenance import Provenance
from mcp_hangar.infrastructure.runtime_store import LoadMetadata, RuntimeMcpServerStore
from mcp_hangar.server import config as server_config
from mcp_hangar.server.config import StoredServerPolicies
from mcp_hangar.server.context import get_context

ID = "math"
DENIED = ToolAccessPolicy(deny_list=("add",))


class _Events:
    def __init__(self) -> None:
        self.names: list[str] = []

    def info(self, event: str, **_: object) -> None:
        self.names.append(event)

    def warning(self, event: str, **_: object) -> None:
        self.names.append(event)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_tool_access_resolver()
    tap_store = SQLiteToolAccessPolicyStore(tmp_path / "auth.db")
    monkeypatch.setattr(get_context(), "auth_components", SimpleNamespace(tap_store=tap_store))
    yield tap_store
    tap_store.close()
    reset_tool_access_resolver()


def _deny(store: SQLiteToolAccessPolicyStore) -> None:
    SetToolAccessPolicyHandler(tap_store=store, event_bus=Mock()).handle(
        SetToolAccessPolicyCommand(scope="provider", target_id=ID, allow_list=[], deny_list=["add"])
    )
    assert _policy() == DENIED


def _policy() -> ToolAccessPolicy | None:
    return get_tool_access_resolver().get_configured_policy("provider", ID)


def _restart(store: SQLiteToolAccessPolicyStore) -> None:
    reset_tool_access_resolver()
    _replay_tap_policies(store)


def _create(repository: InMemoryMcpServerRepository, provenance: Provenance = Provenance.HUMAN) -> None:
    CreateMcpServerHandler(repository, MagicMock()).handle(
        CreateMcpServerCommand(
            mcp_server_id=ID, mode="subprocess", command=["python", "-c", "pass"], provenance=provenance
        )
    )


def _delete(repository: InMemoryMcpServerRepository) -> None:
    DeleteMcpServerHandler(repository, MagicMock(), stored_policies=StoredServerPolicies()).handle(
        DeleteMcpServerCommand(mcp_server_id=ID)
    )


def _unload() -> None:
    runtime_store = RuntimeMcpServerStore()
    hot = MagicMock()
    hot.mcp_server_id = ID
    runtime_store.add(hot, LoadMetadata(loaded_at=datetime.now(), loaded_by=None, source="registry:x", verified=True))
    UnloadMcpServerHandler(runtime_store, MagicMock(), stored_policies=StoredServerPolicies()).handle(
        UnloadMcpServerCommand(mcp_server_id=ID, user_id=None)
    )


class TestTheRestDelete:
    def test_a_server_created_again_without_a_restart_has_none_of_its_policy(self, store) -> None:
        repository = InMemoryMcpServerRepository()
        _create(repository)
        _deny(store)

        _delete(repository)
        _create(repository)

        assert _policy() is None, "the new server inherited the deleted one's policy"

    def test_a_server_created_again_after_a_restart_has_none_of_its_policy(self, store) -> None:
        repository = InMemoryMcpServerRepository()
        _create(repository)
        _deny(store)

        _delete(repository)
        _restart(store)
        _create(repository)

        assert store.get_policy("provider", ID) is None, "the stored row outlived the server"
        assert _policy() is None

    def test_a_refused_delete_keeps_the_policy(self, store) -> None:
        repository = InMemoryMcpServerRepository()
        _create(repository)
        _deny(store)
        handler = DeleteMcpServerHandler(
            repository, MagicMock(), declared_in_config=lambda _: True, stored_policies=StoredServerPolicies()
        )

        with pytest.raises(Exception, match=ID):
            handler.handle(DeleteMcpServerCommand(mcp_server_id=ID))

        assert _policy() == DENIED
        assert store.get_policy("provider", ID) == DENIED

    def test_a_delete_by_discovery_keeps_the_policy(self, store) -> None:
        """A server that flapped out of discovery and back is the same server; its deny list must not lapse."""
        repository = InMemoryMcpServerRepository()
        _create(repository, Provenance.DISCOVERY)
        _deny(store)

        DeleteMcpServerHandler(repository, MagicMock(), stored_policies=StoredServerPolicies()).handle(
            DeleteMcpServerCommand(mcp_server_id=ID, source="discovery:ttl", provenance=Provenance.DISCOVERY)
        )

        assert _policy() == DENIED
        assert store.get_policy("provider", ID) == DENIED


class TestHangarUnload:
    def test_a_server_loaded_again_after_a_restart_has_none_of_its_policy(self, store) -> None:
        _deny(store)

        _unload()
        _restart(store)

        assert store.get_policy("provider", ID) is None, "the stored row outlived the server"
        assert _policy() is None


class TestAStorePeersMayShare:
    @pytest.mark.parametrize("remove", ["delete", "unload"])
    def test_a_lease_keeper_keeps_the_row(self, store, monkeypatch: pytest.MonkeyPatch, remove: str) -> None:
        """A peer whose file still declares the server replays that row; shared storage waits for #2237."""
        coordination = importlib.import_module("mcp_hangar.server.bootstrap.coordination")
        monkeypatch.setattr(coordination, "get_lease_keeper", lambda: object())
        events = _Events()
        monkeypatch.setattr(server_config, "logger", events)
        repository = InMemoryMcpServerRepository()
        _create(repository)
        _deny(store)

        _delete(repository) if remove == "delete" else _unload()

        assert _policy() is None, "the resolver kept the removed server's policy"
        assert store.get_policy("provider", ID) == DENIED
        assert "stored_tool_access_policies_kept_for_peers" in events.names

    def test_a_store_that_is_not_sqlite_keeps_the_row(self, store, monkeypatch: pytest.MonkeyPatch) -> None:
        shared = Mock()
        monkeypatch.setattr(get_context(), "auth_components", SimpleNamespace(tap_store=shared))
        events = _Events()
        monkeypatch.setattr(server_config, "logger", events)

        StoredServerPolicies().forget(ID)

        shared.clear_policy.assert_not_called()
        assert "stored_tool_access_policies_kept_for_peers" in events.names
