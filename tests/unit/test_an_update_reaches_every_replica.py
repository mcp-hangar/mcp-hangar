"""A server's update reaches every replica, without a restart (#1955).

The fleet projection applied a peer's registration and its L7 policy, and, since
#1944, a key-pinned server's connection. A plain server's description, env or
endpoint changed only on the replica that handled the update; the others kept
the old one until they restarted. Here two replicas share one configuration
store and one event log, and replica B learns of A's update only from the tail.
"""

import asyncio
from typing import Any

import pytest

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand, UpdateMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler, UpdateMcpServerHandler
from mcp_hangar.application.event_handlers.fleet_projection import FleetProjection
from mcp_hangar.application.services.event_tailer import EventTailer
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import McpServerRegistered, McpServerUpdated
from mcp_hangar.domain.model import McpServer, McpServerGroup
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore

ENDPOINT = "https://203.0.113.5:8443"
MOVED = "https://203.0.113.9:8443"


class _SyncLoop:
    def run(self, coro, timeout):
        return asyncio.run(coro)


class _Closer:
    def submit(self, coro, on_success=None, on_error=None) -> None:
        asyncio.run(coro)


class _Replica:
    """One gateway: its fleet and groups, the shared row store and event log, its tail."""

    def __init__(self, name: str, configs, log, *, declared: frozenset[str] = frozenset()) -> None:
        self.fleet = InMemoryMcpServerRepository()
        self.groups: dict[str, McpServerGroup] = {}
        self.bus = EventBus()
        self.bus.set_event_store(log)
        projection = FleetProjection(
            self.fleet,
            configs,
            _SyncLoop(),
            closer=_Closer(),
            declared_in_config=declared.__contains__,
            groups=self.groups,
        )
        for event in (McpServerRegistered, McpServerUpdated):
            self.bus.subscribe(event, projection.handle, kind=HandlerKind.PROJECTION)
        self.tailer = EventTailer(log, self.bus, name)
        self.writer = RepositoryFleetWriter(configs)
        self.create = CreateMcpServerHandler(self.fleet, self.bus, fleet_writer=self.writer)
        self.update = UpdateMcpServerHandler(self.fleet, self.bus, fleet_writer=self.writer)


@pytest.fixture
def replicas(tmp_path):
    configs = SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db"))))
    log = SQLiteEventStore(tmp_path / "events.db")
    made: list[_Replica] = []

    def replica(name: str, **kwargs: Any) -> _Replica:
        # Events carry this process's instance id; B's tail, under another
        # name, takes them as a peer's.
        made.append(_Replica(name, configs, log, **kwargs))
        return made[-1]

    yield replica
    for r in made:
        r.writer.close()


def _registered(a: _Replica, b: _Replica, name: str = "payments") -> McpServer:
    a.create.handle(CreateMcpServerCommand(mcp_server_id=name, mode="remote", endpoint=ENDPOINT))
    b.tailer.tick()
    copy = b.fleet.get(name)
    assert copy is not None, "the registration reached B"
    return copy


def test_a_new_endpoint_replaces_the_copy_on_the_other_replica(replicas) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")
    before = _registered(a, b)

    a.update.handle(UpdateMcpServerCommand(mcp_server_id="payments", endpoint=MOVED))
    b.tailer.tick()

    after = b.fleet.get("payments")
    assert after is not before and before.is_retired
    assert after._endpoint == MOVED


def test_a_description_is_taken_in_place_and_nothing_is_echoed(replicas) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")
    copy = _registered(a, b)
    copy.collect_events()

    a.update.handle(UpdateMcpServerCommand(mcp_server_id="payments", description="renamed", idle_ttl_s=120))
    b.tailer.tick()

    assert b.fleet.get("payments") is copy, "no new connection for a field that needs none"
    assert copy.description == "renamed" and copy._idle_ttl.seconds == 120
    assert copy.collect_events() == []


def test_a_group_follows_the_replaced_copy(replicas) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")
    before = _registered(a, b)
    group = McpServerGroup(group_id="pool", auto_start=False)
    group.add_member(before, weight=3)
    b.groups["pool"] = group

    a.update.handle(UpdateMcpServerCommand(mcp_server_id="payments", endpoint=MOVED))
    b.tailer.tick()

    member = group.get_member("payments")
    assert member is not None and member.mcp_server is b.fleet.get("payments")
    assert member.weight == 3


def test_a_server_the_file_declares_changes_only_when_its_replica_reloads(replicas) -> None:
    a = replicas("gateway-a")
    b = replicas("gateway-b", declared=frozenset({"payments"}))
    declared = McpServer(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT, description="from the file")
    b.fleet.add("payments", declared)
    a.fleet.add("payments", McpServer(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT))

    a.update.handle(UpdateMcpServerCommand(mcp_server_id="payments", description="from the api"))
    b.tailer.tick()

    assert b.fleet.get("payments") is declared and declared.description == "from the file"
