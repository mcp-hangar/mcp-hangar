"""A server that leaves the fleet leaves the front-door listing with it (#2088).

The projection was fed on ``McpServerStarted`` and nothing ever took a server's
entries out, so a server removed by a reload, a REST or discovery delete or
``hangar_unload`` stayed listed, and no ``tools/list_changed`` was sent.

The handler is wired through ``subscribe_tool_projection``, the function
bootstrap calls, onto a real ``EventBus``; the registry, the resolver and
``generate_projection`` -- what a tenant's ``tools/list`` serves -- are real. The
repository is the one stand-in. The live counterpart, over streamable HTTP with a
held ``GET /mcp`` stream, is
``tests/integration/test_a_server_a_reload_adds_is_served_on_the_front_door.py``.

Naming: neutral placeholders only (server_a, server_b, read_item, tenant:a).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.application.read_models import tool_projection as tp
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import (
    ConfigurationReloaded,
    McpServerDeregistered,
    McpServerHotUnloaded,
    McpServerStarted,
)
from mcp_hangar.domain.model.tool_catalog import ToolCatalog, ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.fastmcp_server.flat_tool_projection import generate_projection
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.server.bootstrap.event_handlers import subscribe_tool_projection

TENANT = "tenant:a"


def _schema(name: str) -> ToolSchema:
    return ToolSchema(name=name, description=f"Does {name}", input_schema={"type": "object", "properties": {}})


class _Repository:
    """The local fleet: what the handler reads on a start, and checks on a local removal."""

    def __init__(self) -> None:
        self.servers: dict[str, Any] = {}

    def add(self, mcp_server_id: str, tools: list[str]) -> None:
        self.servers[mcp_server_id] = SimpleNamespace(tools=ToolCatalog({t: _schema(t) for t in tools}))

    def get(self, mcp_server_id: str) -> Any:
        return self.servers.get(mcp_server_id)

    def exists(self, mcp_server_id: str) -> bool:
        return mcp_server_id in self.servers

    def remove(self, mcp_server_id: str) -> None:
        self.servers.pop(mcp_server_id, None)


class _Gateway:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.repository = _Repository()
        subscribe_tool_projection(self.bus, self.repository)  # type: ignore[arg-type]

    def start(self, mcp_server_id: str, tools: list[str]) -> None:
        self.repository.add(mcp_server_id, tools)
        self.bus.publish(
            McpServerStarted(
                mcp_server_id=mcp_server_id, mode="subprocess", tools_count=len(tools), startup_duration_ms=1.0
            )
        )

    def reload(self, *, removed: list[str] = (), updated: list[str] = ()) -> None:  # type: ignore[assignment]
        # The reload handler removes from the repository, then publishes.
        for mcp_server_id in removed:
            self.repository.remove(mcp_server_id)
        self.bus.publish(
            ConfigurationReloaded(
                config_path="config.yaml",
                mcp_servers_added=[],
                mcp_servers_removed=list(removed),
                mcp_servers_updated=list(updated),
                mcp_servers_unchanged=[],
                reload_duration_ms=1.0,
                requested_by="tool",
            )
        )

    def delete(self, mcp_server_id: str) -> None:
        self.repository.remove(mcp_server_id)
        self.bus.publish(McpServerDeregistered(mcp_server_id=mcp_server_id, source="api"))


def _listing() -> dict[str, tuple[str, str]]:
    """What the tenant's ``tools/list`` would show: flat name -> (server, tool)."""
    return dict(generate_projection(TENANT).routes)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    get_tool_access_resolver().set_topology_mode("front_door")
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


@pytest.fixture
def told() -> Any:
    """How many times the registry told its listeners -- the front door's ``list_changed`` trigger."""
    calls: list[None] = []

    def listener() -> None:
        calls.append(None)

    tp.add_change_listener(listener)
    yield calls
    tp.remove_change_listener(listener)


@pytest.fixture
def gateway() -> _Gateway:
    gw = _Gateway()
    gw.start("server_a", ["read_item", "get_item"])
    gw.start("server_b", ["list_items"])
    return gw


class TestEachWayOut:
    def test_a_server_a_reload_removes_is_no_longer_listed_and_listeners_are_told(self, gateway, told) -> None:
        assert set(_listing()) == {"read_item", "get_item", "list_items"}

        gateway.reload(removed=["server_a"])

        assert _listing() == {"list_items": ("server_b", "list_items")}
        assert len(told) == 1

    def test_a_deleted_server_is_no_longer_listed(self, gateway, told) -> None:
        gateway.delete("server_a")

        assert _listing() == {"list_items": ("server_b", "list_items")}
        assert len(told) == 1

    def test_a_peers_delete_drops_it_here_too(self, gateway) -> None:
        # A tailed `McpServerDeregistered`: `FleetProjection` drops the server
        # from this replica's fleet on the same event, so its tools go as well.
        gateway.bus.deliver_tailed(McpServerDeregistered(mcp_server_id="server_a", source="api"))

        assert set(_listing()) == {"list_items"}

    def test_a_hot_unload_drops_what_the_server_had_projected(self, gateway) -> None:
        gateway.repository.remove("server_a")

        gateway.bus.publish(McpServerHotUnloaded(mcp_server_id="server_a", user_id=None, lifetime_seconds=1.0))

        assert set(_listing()) == {"list_items"}

    def test_a_hot_loaded_server_is_never_projected_in_the_first_place(self, gateway) -> None:
        # Not in the repository -- it lives in the runtime store -- so its start
        # projects nothing, and the unload above is a guard, not a visible fix.
        gateway.bus.publish(
            McpServerStarted(mcp_server_id="hot", mode="subprocess", tools_count=1, startup_duration_ms=1.0)
        )

        assert get_tool_projection_registry().list_for_server("hot") == []


class TestWhatStays:
    def test_servers_that_stay_and_a_reloads_rebuilt_servers_are_untouched(self, gateway, told) -> None:
        gateway.reload(updated=["server_a"])

        assert set(_listing()) == {"read_item", "get_item", "list_items"}
        assert told == []

    def test_a_removal_the_local_fleet_still_holds_drops_nothing(self, gateway, told) -> None:
        # A hot unload names a server, and a declared one by that name is
        # serving: it keeps its tools.
        gateway.bus.publish(McpServerHotUnloaded(mcp_server_id="server_a", user_id=None, lifetime_seconds=1.0))

        assert set(_listing()) == {"read_item", "get_item", "list_items"}
        assert told == []

    def test_a_group_keeps_its_entry_when_one_member_leaves(self, gateway) -> None:
        gateway.start("search-v1", ["find"])
        gateway.start("search-v2", ["find"])

        def group(*members: str) -> Any:
            ms = [SimpleNamespace(id=m) for m in members]
            return SimpleNamespace(id="search", members=ms, route_candidates=lambda tenant_id: tuple(ms))

        groups = "mcp_hangar.server.bootstrap.composition.GROUPS"
        with patch.dict(groups, {"search": group("search-v1", "search-v2")}, clear=True):
            assert _listing()["find"] == ("search-v1", "find")
        gateway.reload(removed=["search-v1"])
        with patch.dict(groups, {"search": group("search-v2")}, clear=True):
            assert _listing()["find"] == ("search-v2", "find")


class TestComingBack:
    def test_a_server_added_back_under_the_same_name_is_projected_again(self, gateway) -> None:
        gateway.delete("server_a")
        gateway.start("server_a", ["read_item"])

        assert _listing()["read_item"] == ("server_a", "read_item")

    def test_a_withdrawal_outlives_the_removal(self, gateway) -> None:
        # Only the projections go. A server added back by the same name is still
        # withdrawn: clearing the overlays would let it back in ungoverned.
        get_tool_projection_registry().withdraw("server_a", "read_item")
        gateway.delete("server_a")
        gateway.start("server_a", ["read_item", "get_item"])

        assert set(_listing()) == {"get_item", "list_items"}


class TestTheCollisionRule:
    def test_removing_one_of_two_servers_sharing_a_name_lists_the_others_tool(self, gateway, told) -> None:
        # Deliberate: two servers exposing `read_item` hide it from everyone,
        # because a bare name cannot say which one a call means. Once one of
        # them leaves, the name is no longer ambiguous, so it appears and routes
        # to the one that stayed -- and the listing client is told.
        gateway.start("server_c", ["read_item"])
        assert "read_item" not in _listing()
        told.clear()

        gateway.delete("server_c")

        assert _listing()["read_item"] == ("server_a", "read_item")
        assert len(told) == 1


def test_a_delete_runs_on_every_replica_and_a_local_removal_only_where_it_happened() -> None:
    # A delete removes the server from every replica's fleet (`FleetProjection`
    # on the same event), so its projections go everywhere. A reload and a hot
    # unload change only the fleet of the replica that made them.
    bus = MagicMock()
    subscribe_tool_projection(bus, _Repository())  # type: ignore[arg-type]

    kinds = {c.args[0]: c.kwargs["kind"] for c in bus.subscribe.call_args_list}

    assert kinds[McpServerDeregistered] is HandlerKind.PROJECTION
    assert kinds[ConfigurationReloaded] is HandlerKind.LOCAL_VIEW
    assert kinds[McpServerHotUnloaded] is HandlerKind.LOCAL_VIEW
