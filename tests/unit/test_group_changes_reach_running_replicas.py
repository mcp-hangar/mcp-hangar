"""A group change made over REST reaches a running replica (#2172).

Two replicas over one row store and one event log, each with its own fleet and
groups, as `test_a_deleted_server_leaves_every_group_it_was_a_member_of` builds
them. A changes a group through the real command handlers; B's tail delivers the
events to the real projection, which applies the rows by the restart's rules.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any
from unittest.mock import MagicMock

import pytest

from mcp_hangar.application.commands.crud_commands import (
    AddGroupMemberCommand,
    CreateGroupCommand,
    CreateMcpServerCommand,
    DeleteGroupCommand,
    RemoveGroupMemberCommand,
    UpdateGroupCommand,
)
from mcp_hangar.application.commands.crud_handlers import register_crud_handlers
from mcp_hangar.application.event_handlers.fleet_projection import GROUP_EVENTS, FleetProjection, GroupRules
from mcp_hangar.application.group_events import SHARED_GROUP_EVENTS
from mcp_hangar.application.services.event_tailer import EventTailer
from mcp_hangar.auth.policy_projection import ToolAccessPolicyProjection
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import (
    McpServerDeregistered,
    McpServerRegistered,
    ToolAccessPolicyCleared,
    ToolAccessPolicySet,
)
from mcp_hangar.domain.events.producer import current_instance_id
from mcp_hangar.domain.model import LoadBalancerStrategy, McpServerGroup
from mcp_hangar.domain.model.load_balancer import WeightedRoundRobinStrategy
from mcp_hangar.domain.model.mcp_server_group import GroupMemberAdded
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.services import tool_access_resolver as resolver_module
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore
from mcp_hangar.server.config import restore_stored_groups
from mcp_hangar.stream_ids import stream_id_for_event

ENDPOINT = "https://203.0.113.5:8443"


@pytest.fixture(autouse=True)
def _no_upstream_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """A member added through the handler starts at once; no upstream listens here."""
    monkeypatch.setattr(McpServerGroup, "_try_start_member_unlocked", lambda *_: False)


class _SyncLoop:
    def run(self, coro: Any, timeout: float) -> Any:
        return asyncio.run(coro)


class _CountingRows:
    """The shared row store, counting group reads and failing them on request."""

    def __init__(self, rows: SQLiteMcpServerConfigRepository) -> None:
        self._rows = rows
        self.reads = 0
        self.unreachable = False
        self.after_read: Any = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._rows, name)

    async def read_groups(self) -> Any:
        self.reads += 1
        if self.unreachable:
            raise ConnectionError("the store went away")
        stored = await self._rows.read_groups()
        if self.after_read is not None:
            await self.after_read()
        return stored


class _Replica:
    def __init__(self, name: str, rows: _CountingRows, log: SQLiteEventStore, declared: dict[str, frozenset[str]]):
        self.fleet = InMemoryMcpServerRepository()
        self.groups: dict[str, McpServerGroup] = {}
        self.bus = EventBus()
        self.bus.set_event_store(log)
        self.projection = FleetProjection(
            self.fleet,
            rows,
            _SyncLoop(),
            groups=self.groups,
            instance_id=name,
            group_rules=GroupRules(
                restore=partial(restore_stored_groups, repository=self.fleet, groups=self.groups),
                drop=lambda group_id: self.groups.pop(group_id, None) and None,
                declared_members=declared.get,
            ),
        )
        for event in (McpServerRegistered, McpServerDeregistered, *GROUP_EVENTS):
            self.bus.subscribe(event, self.projection.handle, kind=HandlerKind.PROJECTION)
        self.tailer = EventTailer(log, self.bus, name)
        self.writer = RepositoryFleetWriter(rows)
        self.commands = CommandBus()
        register_crud_handlers(self.commands, self.fleet, self.bus, self.groups, self.writer, group_writer=self.writer)


@pytest.fixture
def rows(tmp_path) -> _CountingRows:
    return _CountingRows(SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db")))))


@pytest.fixture
def replicas(tmp_path, rows):
    log = SQLiteEventStore(tmp_path / "events.db")
    made: list[_Replica] = []

    def replica(name: str, declared: dict[str, frozenset[str]] | None = None) -> _Replica:
        made.append(_Replica(name, rows, log, declared or {}))
        return made[-1]

    yield replica
    for r in made:
        r.writer.close()


def _server(replica: _Replica, name: str) -> None:
    replica.commands.send(CreateMcpServerCommand(mcp_server_id=name, mode="remote", endpoint=ENDPOINT))


def _pool(a: _Replica, b: _Replica, member: str = "payments") -> McpServerGroup:
    """A group with a member, made on A and held by B."""
    _server(a, member)
    a.commands.send(
        CreateGroupCommand(group_id="pool", strategy="weighted_round_robin", min_healthy=2, description="from REST")
    )
    a.commands.send(AddGroupMemberCommand(group_id="pool", mcp_server_id=member, weight=3, priority=2))
    b.tailer.tick()
    return b.groups["pool"]


def _shared_events(group: McpServerGroup) -> list[Any]:
    return [event for event in group.collect_events() if type(event) in SHARED_GROUP_EVENTS]


def test_a_group_and_its_member_reach_a_running_replica(replicas) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")

    group = _pool(a, b)

    assert group.strategy is LoadBalancerStrategy.WEIGHTED_ROUND_ROBIN
    assert group.description == "from REST"
    assert group.to_status_dict()["min_healthy"] == 2
    member = group.get_member("payments")
    assert member is not None and member.mcp_server is b.fleet.get("payments")
    assert (member.weight, member.priority) == (3, 2)


def test_a_group_update_reaches_a_running_replica_and_each_routes_by_the_new_strategy(replicas) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")
    a.commands.send(CreateGroupCommand(group_id="pool"))
    b.tailer.tick()

    a.commands.send(
        UpdateGroupCommand(group_id="pool", strategy="weighted_round_robin", min_healthy=3, description="moved")
    )
    b.tailer.tick()

    group = b.groups["pool"]
    assert group.strategy is LoadBalancerStrategy.WEIGHTED_ROUND_ROBIN
    assert group.description == "moved" and group.to_status_dict()["min_healthy"] == 3
    for held in (group, a.groups["pool"]):
        assert isinstance(held._load_balancer._impl, WeightedRoundRobinStrategy)


def test_a_removed_member_and_a_deleted_group_leave_a_running_replica_and_the_server_stays(replicas) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")
    group = _pool(a, b)

    a.commands.send(RemoveGroupMemberCommand(group_id="pool", mcp_server_id="payments"))
    b.tailer.tick()
    assert group.get_member("payments") is None

    a.commands.send(DeleteGroupCommand(group_id="pool"))
    b.tailer.tick()
    assert "pool" not in b.groups
    assert b.fleet.get("payments") is not None


def test_the_tail_delivering_an_event_twice_changes_nothing(replicas) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")
    group = _pool(a, b)
    before = group.get_member("payments")

    event = GroupMemberAdded(group_id="pool", member_id="payments", weight=3, priority=2)
    b.bus.deliver_tailed(event)

    assert b.groups["pool"] is group and group.get_member("payments") is before and group.total_count == 1


def test_a_replica_reads_no_rows_for_its_own_change(replicas, rows) -> None:
    a = replicas(current_instance_id())  # Every event a test raises carries the process's id.
    _server(a, "payments")

    a.commands.send(CreateGroupCommand(group_id="pool"))
    a.commands.send(AddGroupMemberCommand(group_id="pool", mcp_server_id="payments"))

    assert rows.reads == 0
    assert a.groups["pool"].total_count == 1


def test_applying_a_peers_change_puts_nothing_back_on_the_log(replicas) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")
    group = _pool(a, b)
    a.commands.send(UpdateGroupCommand(group_id="pool", description="moved"))
    a.commands.send(RemoveGroupMemberCommand(group_id="pool", mcp_server_id="payments"))
    b.tailer.tick()

    assert _shared_events(group) == []


def test_the_file_wins_for_what_it_declares(replicas) -> None:
    a = replicas("gateway-a", {"pool": frozenset({"file-member"})})
    b = replicas("gateway-b", {"pool": frozenset({"file-member"})})
    for replica in (a, b):
        replica.groups["pool"] = McpServerGroup("pool", LoadBalancerStrategy.ROUND_ROBIN, description="from the file")
    _server(a, "file-member")
    _server(a, "added")
    b.tailer.tick()
    b.groups["pool"].restore_member(b.fleet.get("file-member"), 1, 1)

    a.commands.send(UpdateGroupCommand(group_id="pool", strategy="weighted_round_robin", description="from REST"))
    a.commands.send(AddGroupMemberCommand(group_id="pool", mcp_server_id="added"))
    b.tailer.tick()
    group = b.groups["pool"]
    assert group.strategy is LoadBalancerStrategy.ROUND_ROBIN and group.description == "from the file"
    assert group.get_member("added") is not None, "a member added over REST follows its row"

    a.commands.send(RemoveGroupMemberCommand(group_id="pool", mcp_server_id="added"))
    a.commands.send(DeleteGroupCommand(group_id="pool"))
    b.tailer.tick()
    assert b.groups["pool"] is group
    assert [m.id for m in group.members] == ["file-member"]


def test_a_member_whose_server_arrives_later_joins_when_it_does(replicas) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")
    _server(a, "payments")
    a.commands.send(CreateGroupCommand(group_id="pool"))
    a.commands.send(AddGroupMemberCommand(group_id="pool", mcp_server_id="payments", weight=4))

    # The group's stream first: what the log's order between two streams allows.
    added = GroupMemberAdded(group_id="pool", member_id="payments", weight=4, priority=1)
    b.projection.handle(added)
    assert b.groups["pool"].get_member("payments") is None

    b.tailer.tick()
    member = b.groups["pool"].get_member("payments")
    assert member is not None and member.weight == 4


def test_a_request_here_racing_a_peers_change_keeps_the_member_it_added(replicas, rows) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")
    _pool(a, b)
    _server(a, "added-here")
    b.tailer.tick()

    async def b_adds_a_member() -> None:
        # In a thread, as a request is: the projection's loop is waiting on this read.
        rows.after_read = None
        command = AddGroupMemberCommand(group_id="pool", mcp_server_id="added-here")
        await asyncio.to_thread(b.commands.send, command)

    rows.after_read = b_adds_a_member
    a.commands.send(RemoveGroupMemberCommand(group_id="pool", mcp_server_id="payments"))
    b.tailer.tick()

    group = b.groups["pool"]
    assert group.get_member("added-here") is not None, "the snapshot predates it, and no event names it"
    assert group.get_member("payments") is None


def test_a_row_naming_a_server_never_held_costs_no_read_on_later_events(replicas, rows) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")
    a.commands.send(CreateGroupCommand(group_id="pool"))
    _server(a, "payments")
    a.commands.send(AddGroupMemberCommand(group_id="pool", mcp_server_id="payments"))
    b.projection.handle(GroupMemberAdded(group_id="pool", member_id="payments", weight=1, priority=1))
    reads = rows.reads

    _server(a, "unrelated")
    b.projection.handle(McpServerRegistered("unrelated", "api", "remote"))

    assert rows.reads == reads


def test_rows_that_could_not_be_read_are_read_on_the_next_event(replicas, rows) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")
    a.commands.send(CreateGroupCommand(group_id="pool"))

    rows.unreachable = True
    b.tailer.tick()
    assert "pool" not in b.groups

    rows.unreachable = False
    a.commands.send(CreateGroupCommand(group_id="other"))
    b.tailer.tick()
    assert {"pool", "other"} <= set(b.groups)


def test_one_malformed_row_leaves_the_other_groups_applied(replicas) -> None:
    a, b = replicas("gateway-a"), replicas("gateway-b")
    a.commands.send(CreateGroupCommand(group_id="aaa"))
    a.commands.send(CreateGroupCommand(group_id="pool"))
    b.tailer.tick()
    asyncio.run(a.writer._group_rows.update_group("aaa", "no-such-strategy", None, None))

    a.commands.send(UpdateGroupCommand(group_id="aaa", description="moved"))
    a.commands.send(UpdateGroupCommand(group_id="pool", description="moved"))
    b.tailer.tick()

    assert b.groups["aaa"].strategy is LoadBalancerStrategy.ROUND_ROBIN
    assert b.groups["pool"].description == "moved"


class TestToolAccessPolicy:
    @pytest.fixture
    def resolver(self, monkeypatch: pytest.MonkeyPatch) -> ToolAccessResolver:
        resolver = ToolAccessResolver()
        monkeypatch.setattr(resolver_module, "_resolver", resolver, raising=False)
        monkeypatch.setattr(resolver_module, "get_tool_access_resolver", lambda: resolver)
        monkeypatch.setattr("mcp_hangar.auth.policy_projection.get_tool_access_resolver", lambda: resolver)
        return resolver

    @staticmethod
    def _set() -> ToolAccessPolicySet:
        return ToolAccessPolicySet(scope="group", target_id="pool", allow_list=[], deny_list=["add"])

    def test_a_peers_policy_is_read_back_whole_and_applied(self, resolver: ToolAccessResolver) -> None:
        store = MagicMock()
        store.get_policy.return_value = ToolAccessPolicy(deny_list=("add",), approval_list=("pay",))

        ToolAccessPolicyProjection(store, "gateway-b").handle(self._set())

        applied = resolver.get_configured_policy("group", "pool")
        assert applied is not None and applied.deny_list == ("add",) and applied.approval_list == ("pay",)

    def test_this_replicas_own_policy_is_not_read_back(self, resolver: ToolAccessResolver) -> None:
        store = MagicMock()

        ToolAccessPolicyProjection(store).handle(self._set())

        store.get_policy.assert_not_called()

    @pytest.mark.parametrize("read", [{"return_value": None}, {"side_effect": ConnectionError("gone")}])
    def test_with_no_row_to_read_the_local_policy_stays(
        self, resolver: ToolAccessResolver, read: dict[str, Any]
    ) -> None:
        in_force = ToolAccessPolicy(deny_list=("other",))
        resolver.set_group_policy("pool", in_force)
        store = MagicMock()
        store.get_policy.configure_mock(**read)

        ToolAccessPolicyProjection(store, "gateway-b").handle(self._set())

        assert resolver.get_configured_policy("group", "pool") == in_force


@pytest.mark.parametrize(
    "event",
    [
        ToolAccessPolicySet(scope="group", target_id="pool", allow_list=[], deny_list=["add"]),
        ToolAccessPolicyCleared(scope="group", target_id="pool"),
    ],
)
def test_a_tool_access_policy_change_goes_on_the_log(event: Any) -> None:
    """The tail reads only what has a stream, and a peer hears of a change from the tail."""
    assert stream_id_for_event(event) == "tool_access_policy:group:pool"
