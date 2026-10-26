"""A deleted server leaves every group it was a member of, and a server of its name joins in its place (#2107).

A group member holds its server by reference. Deleting the server -- over the
REST API, or by discovery when its TTL runs out -- stopped it and took it out
of the repository, but the group kept the stopped copy in rotation. A COLD
member in rotation is selected, and selecting it starts it: a call through the
group launched the deleted server's command again, outside the repository,
where nothing managed it. On a peer, the fleet projection removed the copy
without telling the groups, and without closing its client.

The member stays in the group, vacant: members are declared by id, as in the
configuration file, and the next server registered under that id fills it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand, DeleteMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import DeleteMcpServerHandler, register_crud_handlers
from mcp_hangar.application.event_handlers.fleet_projection import FleetProjection
from mcp_hangar.application.read_models.tool_projection import reset_tool_projection_registry
from mcp_hangar.application.services.event_tailer import EventTailer
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import McpServerDeregistered, McpServerRegistered, McpServerUpdated
from mcp_hangar.domain.exceptions import ConfigurationError, McpServerNotReadyError
from mcp_hangar.domain.model import McpServer, McpServerGroup
from mcp_hangar.domain.model.mcp_server_group import RouteReason
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.domain.value_objects.peer_identity import PeerIdentity
from mcp_hangar.domain.value_objects.provenance import Provenance
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore
from mcp_hangar.server import config as server_config
from mcp_hangar.server.bootstrap import discovery as bootstrap_discovery
from mcp_hangar.server.config import load_config
from mcp_hangar.server.state import GROUPS, get_runtime

ENDPOINT = "https://203.0.113.5:8443"
MOVED = "https://203.0.113.9:8443"


class _Client:
    def __init__(self) -> None:
        self.closed = False

    def is_alive(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def launches(monkeypatch) -> list[McpServer]:
    """Every copy that tried to start. A start is refused right after, so nothing runs."""
    started: list[McpServer] = []

    def create_client(self: McpServer) -> Any:
        started.append(self)
        raise RuntimeError("no upstream in this test")

    monkeypatch.setattr(McpServer, "_create_client", create_client)
    return started


def _call_through(group: McpServerGroup) -> McpServer | None:
    """What a call through the group does: select a member, then start it if it is not running."""
    selected = group.select_member_with_reason(None).member
    if selected is not None:
        try:
            selected.ensure_ready()
        except Exception:  # noqa: BLE001 -- the start is what is observed, not its outcome
            pass
    return selected


class _Gateway:
    """One replica's command side, wired as bootstrap wires it."""

    def __init__(self) -> None:
        self.repository = InMemoryMcpServerRepository()
        self.groups: dict[str, McpServerGroup] = {}
        self.events = EventBus()
        self.commands = CommandBus()
        register_crud_handlers(self.commands, self.repository, self.events, self.groups)

    def create(self, name: str, **kwargs: Any) -> McpServer:
        self.commands.send(
            CreateMcpServerCommand(mcp_server_id=name, mode="subprocess", command=["python", "-m", name], **kwargs)
        )
        server = self.repository.get(name)
        assert isinstance(server, McpServer)
        return server

    def pool(self, *names: str) -> McpServerGroup:
        """A group of *names*, each in rotation and COLD: a member a call starts on first use."""
        group = McpServerGroup(group_id="pool", auto_start=False)
        for name in names:
            group.add_member(self.repository.get(name))
            member = group.get_member(name)
            assert member is not None
            member.in_rotation = True
        self.groups["pool"] = group
        return group


@pytest.fixture
def gateway() -> _Gateway:
    return _Gateway()


class TestADeleteOverTheApi:
    def test_a_call_through_the_group_never_starts_the_deleted_server(self, gateway, launches) -> None:
        gateway.create("left")
        group = gateway.pool("left")

        gateway.commands.send(DeleteMcpServerCommand(mcp_server_id="left", source="api"))

        assert _call_through(group) is None
        assert launches == []
        assert group.select_member_with_reason(None).reason is RouteReason.NO_AVAILABLE_MEMBER

    def test_no_other_path_starts_it_either(self, gateway, launches) -> None:
        deleted = gateway.create("left")
        group = gateway.pool("left")

        gateway.commands.send(DeleteMcpServerCommand(mcp_server_id="left", source="api"))

        assert group.route_candidates(None) == ()
        assert group.recovery_candidates() == []
        group.rebalance()
        assert group.route_candidates(None) == ()
        assert group.start_all() == 0
        with pytest.raises(McpServerNotReadyError):
            deleted.ensure_ready()
        assert launches == []

    def test_the_other_members_are_routed_to_as_before(self, gateway, launches) -> None:
        stays = gateway.create("stays")
        gateway.create("left")
        group = gateway.pool("stays", "left")

        gateway.commands.send(DeleteMcpServerCommand(mcp_server_id="left", source="api"))

        assert {_call_through(group) for _ in range(4)} == {stays}
        assert launches and set(launches) == {stays}
        assert group.circuit_open is False

    def test_the_member_stays_in_the_group_vacant(self, gateway) -> None:
        gateway.create("stays")
        gateway.create("left")
        group = gateway.pool("stays", "left")

        gateway.commands.send(DeleteMcpServerCommand(mcp_server_id="left", source="api"))

        status = {m["id"]: m for m in group.to_status_dict()["members"]}
        assert status["left"]["vacant"] is True and status["left"]["in_rotation"] is False
        assert status["stays"]["vacant"] is False and status["stays"]["in_rotation"] is True
        assert group.total_count == 2 and group.members_in_rotation_count == 1

    def test_a_group_that_loses_its_only_member_is_inactive(self, gateway) -> None:
        gateway.create("left")
        group = gateway.pool("left")
        group.rebalance()

        gateway.commands.send(DeleteMcpServerCommand(mcp_server_id="left", source="api"))

        assert group.to_status_dict()["state"] == "inactive"
        assert group.is_available is False


class TestARecreatedServer:
    def test_the_group_routes_to_the_new_copy(self, gateway, launches) -> None:
        old = gateway.create("left")
        group = gateway.pool("left")
        gateway.commands.send(DeleteMcpServerCommand(mcp_server_id="left", source="api"))

        new = gateway.create("left")

        assert new is not old
        assert _call_through(group) is new
        assert launches == [new]
        member = group.get_member("left")
        assert member is not None and member.mcp_server is new and not member.vacant

    def test_a_member_never_in_rotation_stays_out_of_it(self, gateway) -> None:
        gateway.create("left")
        group = McpServerGroup(group_id="pool", auto_start=False)
        group.add_member(gateway.repository.get("left"))
        gateway.groups["pool"] = group
        gateway.commands.send(DeleteMcpServerCommand(mcp_server_id="left", source="api"))

        gateway.create("left")

        member = group.get_member("left")
        assert member is not None and member.mcp_server is gateway.repository.get("left")
        assert member.in_rotation is False

    def test_a_key_pinned_server_does_not_fill_the_member(self) -> None:
        repository = InMemoryMcpServerRepository()
        events = EventBus()
        old = McpServer(mcp_server_id="left", mode="remote", endpoint=ENDPOINT)
        repository.add("left", old)
        group = McpServerGroup(group_id="pool", auto_start=False)
        group.add_member(old)
        groups = {"pool": group}
        DeleteMcpServerHandler(repository, events, groups=groups).handle(DeleteMcpServerCommand(mcp_server_id="left"))

        pinned = McpServer(mcp_server_id="left", mode="remote", endpoint=ENDPOINT)
        # What the check reads: a key-pinned create needs a durable store and an authority.
        pinned._peer_identity = PeerIdentity(key_ref="payments-key", spki_sha256=frozenset({"sha256:" + "a" * 64}))

        from mcp_hangar.application.group_membership import fill_vacant_member

        fill_vacant_member(groups, "left", pinned)

        member = group.get_member("left")
        assert member is not None and member.vacant and member.mcp_server is old


class TestADiscoveredServerWhoseTtlRanOut:
    def test_it_leaves_its_group_and_comes_back_into_it(self, gateway, launches, monkeypatch) -> None:
        monkeypatch.setattr(
            bootstrap_discovery,
            "get_runtime",
            lambda: SimpleNamespace(repository=gateway.repository, command_bus=gateway.commands),
        )
        old = gateway.create("svc", provenance=Provenance.DISCOVERY)
        group = gateway.pool("svc")

        asyncio.run(bootstrap_discovery._on_mcp_server_deregister("svc", "ttl_expired"))

        assert gateway.repository.get("svc") is None
        assert _call_through(group) is None and launches == []

        new = gateway.create("svc", provenance=Provenance.DISCOVERY)
        assert _call_through(group) is new and new is not old


# --- On a peer ---------------------------------------------------------------


class _SyncLoop:
    def run(self, coro, timeout):
        return asyncio.run(coro)


class _Closer:
    def submit(self, coro, on_success=None, on_error=None) -> None:
        asyncio.run(coro)


class _Replica:
    """One gateway: its fleet and groups, the shared row store and event log, its tail."""

    def __init__(self, name: str, configs, log) -> None:
        self.fleet = InMemoryMcpServerRepository()
        self.groups: dict[str, McpServerGroup] = {}
        self.bus = EventBus()
        self.bus.set_event_store(log)
        projection = FleetProjection(self.fleet, configs, _SyncLoop(), closer=_Closer(), groups=self.groups)
        for event in (McpServerRegistered, McpServerUpdated, McpServerDeregistered):
            self.bus.subscribe(event, projection.handle, kind=HandlerKind.PROJECTION)
        self.tailer = EventTailer(log, self.bus, name)
        self.writer = RepositoryFleetWriter(configs)
        self.commands = CommandBus()
        register_crud_handlers(self.commands, self.fleet, self.bus, self.groups, self.writer)


@pytest.fixture
def replicas(tmp_path):
    configs = SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db"))))
    log = SQLiteEventStore(tmp_path / "events.db")
    made: list[_Replica] = []

    def replica(name: str) -> _Replica:
        made.append(_Replica(name, configs, log))
        return made[-1]

    yield replica
    for r in made:
        r.writer.close()


def _peer_pool(a: _Replica, b: _Replica, **kwargs: Any) -> tuple[McpServerGroup, McpServer, _Client]:
    a.commands.send(CreateMcpServerCommand(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT, **kwargs))
    b.tailer.tick()
    copy = b.fleet.get("payments")
    assert isinstance(copy, McpServer), "the registration reached B"
    client = _Client()
    copy._client = client
    group = McpServerGroup(group_id="pool", auto_start=False)
    group.add_member(copy, weight=3)
    member = group.get_member("payments")
    assert member is not None
    member.in_rotation = True
    b.groups["pool"] = group
    return group, copy, client


class TestOnAPeer:
    def test_a_delete_vacates_the_member_and_closes_the_old_client(self, replicas, launches) -> None:
        a, b = replicas("gateway-a"), replicas("gateway-b")
        group, copy, client = _peer_pool(a, b)

        a.commands.send(DeleteMcpServerCommand(mcp_server_id="payments", source="api"))
        b.tailer.tick()

        assert b.fleet.get("payments") is None
        assert copy.is_retired and client.closed
        assert _call_through(group) is None and launches == []

    def test_a_server_registered_again_fills_the_member(self, replicas, launches) -> None:
        a, b = replicas("gateway-a"), replicas("gateway-b")
        group, copy, _ = _peer_pool(a, b)
        a.commands.send(DeleteMcpServerCommand(mcp_server_id="payments", source="api"))
        b.tailer.tick()

        a.commands.send(CreateMcpServerCommand(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT))
        b.tailer.tick()

        new = b.fleet.get("payments")
        assert isinstance(new, McpServer) and new is not copy
        member = group.get_member("payments")
        assert member is not None and member.mcp_server is new and member.weight == 3
        assert _call_through(group) is new and launches == [new]

    def test_a_replaced_discovered_server_is_followed_and_its_old_client_closed(self, replicas, launches) -> None:
        a, b = replicas("gateway-a"), replicas("gateway-b")
        group, copy, client = _peer_pool(a, b, provenance=Provenance.DISCOVERY)

        a.commands.send(
            CreateMcpServerCommand(
                mcp_server_id="payments",
                mode="remote",
                endpoint=MOVED,
                provenance=Provenance.DISCOVERY,
                replace_discovered=True,
            )
        )
        b.tailer.tick()

        new = b.fleet.get("payments")
        assert isinstance(new, McpServer) and new is not copy and new._endpoint == MOVED
        assert copy.is_retired and client.closed
        member = group.get_member("payments")
        assert member is not None and member.mcp_server is new
        assert member.weight == 3 and member.in_rotation and not member.vacant
        assert _call_through(group) is new and launches == [new]

    def test_a_retired_copy_reading_ready_is_not_put_back_by_a_rebalance(self, replicas) -> None:
        a, b = replicas("gateway-a"), replicas("gateway-b")
        group, copy, _ = _peer_pool(a, b)
        copy._state = McpServerState.READY

        a.commands.send(DeleteMcpServerCommand(mcp_server_id="payments", source="api"))
        b.tailer.tick()
        group.rebalance()

        assert group.route_candidates(None) == ()
        assert group.healthy_count == 0
        assert group.to_status_dict()["members"][0]["state"] == "cold"


# --- What a reload does, for comparison ----------------------------------------

TOP_LEVEL = {"mode": "subprocess", "command": ["python", "-m", "some.server"]}


@pytest.fixture
def staged_groups():
    def reset() -> None:
        reset_tool_access_resolver()
        reset_tool_projection_registry()
        server_config._BUILT_FROM.clear()
        repository = get_runtime().repository
        for mcp_server_id in ("m1", "m2"):
            if repository.exists(mcp_server_id):
                repository.remove(mcp_server_id)

    original = dict(GROUPS)
    GROUPS.clear()
    reset()
    yield
    reset()
    GROUPS.clear()
    GROUPS.update(original)


def _config(*members: str, servers: tuple[str, ...] = ("m1", "m2")) -> dict[str, Any]:
    config: dict[str, Any] = {name: dict(TOP_LEVEL) for name in servers}
    config["pool"] = {"mode": "group", "auto_start": False, "members": [{"id": m} for m in members]}
    return config


@pytest.mark.usefixtures("staged_groups")
class TestAReloadAsItIs:
    def test_a_member_the_file_no_longer_lists_is_gone_from_the_group(self) -> None:
        load_config(_config("m1", "m2"), replace=True)

        load_config(_config("m1", servers=("m1",)), replace=True)

        assert [m.id for m in GROUPS["pool"].members] == ["m1"]

    def test_a_member_whose_server_the_file_dropped_is_refused(self) -> None:
        load_config(_config("m1", "m2"), replace=True)
        running = GROUPS["pool"]

        with pytest.raises(ConfigurationError):
            load_config(_config("m1", "m2", servers=("m1",)), replace=True)

        assert GROUPS["pool"] is running
        member = running.get_member("m2")
        assert member is not None and member.mcp_server is get_runtime().repository.get("m2")
