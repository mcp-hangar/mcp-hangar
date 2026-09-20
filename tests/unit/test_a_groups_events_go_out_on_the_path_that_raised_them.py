"""A group's events go out on the path that raised them, and where they belong (#1410).

Until this, the only thing that drained a group was the group CRUD handlers.
Everything a group recorded while serving -- a member out of rotation, the
circuit opened, a rebalance -- sat on the aggregate until somebody edited the
group, and if nobody did, until the process ended.

Two things are pinned here, and they are different claims:

**When.** The executor drains after it reports a call's outcome, the rebalance
saga after it reports a health check, and ``hangar_group_rebalance`` after it
rebalances. A subscriber hears the events on that path. The served path is
proved in
``tests/integration/test_a_groups_events_reach_a_subscriber_on_the_call.py``.

**Where.** A group's rotation and circuit breaker are this replica's own, so
the events about them are published locally and never appended to the shared
log; its configuration is the same on every replica and still goes to the
group's stream. `REPLICA_LOCAL_GROUP_EVENTS` and `SHARED_GROUP_EVENTS` are that
split, and every event a group records is in exactly one of them.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar._sdk_compat import FastMCP
from mcp_hangar.application.commands.crud_commands import CreateGroupCommand, UpdateGroupCommand
from mcp_hangar.application.commands.crud_handlers import CreateGroupHandler, UpdateGroupHandler
from mcp_hangar.application.group_events import (
    publish_group_events,
    REPLICA_LOCAL_GROUP_EVENTS,
    SHARED_GROUP_EVENTS,
)
from mcp_hangar.application.read_models.tool_projection import reset_tool_projection_registry
from mcp_hangar.application.sagas import GroupRebalanceSaga
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import CircuitBreakerStateChanged, DomainEvent, HealthCheckPassed
from mcp_hangar.domain.exceptions import RateLimitExceeded
from mcp_hangar.domain.model import mcp_server_group as group_module
from mcp_hangar.domain.model.mcp_server_group import (
    GroupCircuitClosed,
    GroupCircuitOpened,
    GroupMemberHealthChanged,
    GroupStateChanged,
    GroupUpdated,
    McpServerGroup,
)
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects import GroupState, McpServerState
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec
from mcp_hangar.server.tools.groups import register_group_tools
from mcp_hangar.stream_ids import MCP_SERVER_GROUP, stream_id_for

_GROUP, _MEMBER, _TOOL = "pool", "svc", "t"


class _RecordingBus:
    """Records the two ways a drain can publish, and keeps them apart."""

    def __init__(self) -> None:
        self.local: list[DomainEvent] = []
        self.published: list[DomainEvent] = []
        self.appended: list[tuple[str, str, list[DomainEvent]]] = []

    def publish_local(self, event: DomainEvent) -> None:
        self.local.append(event)

    def publish(self, event: DomainEvent) -> None:
        # The batch's own lifecycle events come this way. A group's must not:
        # `publish` derives the stream from the event, so a group event taking
        # this door would be appended to the group's stream after all.
        self.published.append(event)

    def publish_aggregate_events(self, aggregate_type: str, aggregate_id: str, events: list[DomainEvent]) -> int:
        self.appended.append((aggregate_type, aggregate_id, list(events)))
        return len(events) - 1

    @property
    def local_types(self) -> list[type[DomainEvent]]:
        return [type(event) for event in self.local]

    @property
    def appended_types(self) -> list[type[DomainEvent]]:
        return [type(event) for _t, _i, batch in self.appended for event in batch]

    @property
    def group_events_via_publish(self) -> list[DomainEvent]:
        """Any group event that took the recording door -- the other way onto the log."""
        return [e for e in self.published if isinstance(e, tuple(REPLICA_LOCAL_GROUP_EVENTS | SHARED_GROUP_EVENTS))]


class _Id(str):
    """A member id that reads as a string and carries ``.value``, as `McpServerId` does.

    Both spellings are load-bearing: `McpServerGroup.add_member` keys the member
    by ``str(mcp_server.id)`` and the executor reports it by ``id.value``. A
    double that answers only one of them reports a member the group has never
    heard of, and every assertion below would pass against nothing happening.
    """

    @property
    def value(self) -> str:
        return str(self)


def _server(server_id: str = _MEMBER) -> MagicMock:
    server = MagicMock()
    server.id = _Id(server_id)
    server.mcp_server_id = server_id
    server.state = Mock(value="ready")
    server.state_snapshot = McpServerState.READY
    server.dead_reason_snapshot = None
    server.health = Mock(should_degrade=Mock(return_value=False))
    return server


def _group(**kwargs: Any) -> McpServerGroup:
    """One member in rotation, and a group that reacts to the first counted failure.

    With both thresholds at one, a single failing call takes the member out of
    rotation *and* opens the circuit, so one call raises every kind of event
    this module sorts.
    """
    group = McpServerGroup(
        group_id=_GROUP,
        auto_start=False,
        min_healthy=1,
        unhealthy_threshold=1,
        healthy_threshold=1,
        circuit_failure_threshold=1,
        **kwargs,
    )
    group.add_member(_server())
    member = group.get_member(_MEMBER)
    assert member is not None, "the group keyed its member by something else"
    member.in_rotation = True
    # Settle the state the rotation implies before draining, so what a test
    # does next is the only thing that records anything. Without this the group
    # is still INACTIVE and the first success of any kind moves it to HEALTHY.
    group.report_success(_MEMBER)
    group.collect_events()
    assert group.state is GroupState.HEALTHY and group.circuit_open is False
    return group


def _tripped() -> McpServerGroup:
    """A group driven out and open, with the events that did it already drained."""
    group = _group()
    group.report_failure(_MEMBER)
    assert group.circuit_open is True
    group.collect_events()
    return group


# ----------------------------------------------------------------------------
# The call path: the executor drains after it reports the outcome
# ----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons():
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


@pytest.fixture()
def group_call():
    """Run one call through a real group; what the bus heard by the time it returned."""
    bus = _RecordingBus()
    ctx = Mock()
    ctx.event_bus = bus
    ctx.get_mcp_server.return_value = None  # a group is not in the server repository
    ctx.mcp_server_exists.return_value = False

    def run(group: McpServerGroup, invoke: BaseException | None = None):
        def send(command: Any) -> Any:
            if invoke is not None and type(command).__name__ == "InvokeToolCommand":
                raise invoke
            return {"ok": True}

        ctx.command_bus.send.side_effect = send
        call = CallSpec(index=0, call_id="c1", mcp_server=_GROUP, tool=_TOOL, arguments={}, max_retries=1)
        batch = BatchExecutor().execute(
            batch_id="b", calls=[call], max_concurrency=1, global_timeout=30.0, fail_fast=False
        )
        return batch.results[0], bus

    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as executor_groups,
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as validator_groups,
    ):
        executor_groups.get.side_effect = lambda group_id: _held.get(group_id)
        validator_groups.get.side_effect = lambda group_id: _held.get(group_id)
        _held: dict[str, McpServerGroup] = {}

        def run_with(group: McpServerGroup, invoke: BaseException | None = None):
            _held[_GROUP] = group
            return run(group, invoke)

        yield run_with


class TestTheCallPathPublishesWhatItRaised:
    def test_a_member_failing_out_and_the_circuit_opening_go_out_on_that_call(self, group_call) -> None:
        group = _group()

        result, bus = group_call(group, invoke=RuntimeError("backend down"))

        assert result.success is False
        # Everything the group made of the outcome, heard before execute returned.
        assert GroupMemberHealthChanged in bus.local_types
        assert GroupCircuitOpened in bus.local_types
        assert GroupStateChanged in bus.local_types
        assert CircuitBreakerStateChanged in bus.local_types
        [left] = [e for e in bus.local if isinstance(e, GroupMemberHealthChanged)]
        assert left.member_id == _MEMBER and left.in_rotation is False

    def test_nothing_is_waiting_on_the_aggregate_afterwards(self, group_call) -> None:
        group = _group()

        group_call(group, invoke=RuntimeError("backend down"))

        assert group.has_uncommitted_events() is False

    def test_none_of_it_reaches_the_shared_log(self, group_call) -> None:
        group = _group()

        _result, bus = group_call(group, invoke=RuntimeError("backend down"))

        assert bus.appended == []
        # Nor through `publish`, which derives the stream from the event and
        # would have appended them to the group's stream just the same.
        assert bus.group_events_via_publish == []

    def test_a_healthy_call_publishes_nothing_because_nothing_changed(self, group_call) -> None:
        """A success on a group already healthy records no event, so the drain has none."""
        group = _group()

        result, bus = group_call(group)

        assert result.success is True
        assert bus.local == [] and bus.appended == []

    def test_a_refusal_hangar_owns_tells_the_group_nothing_and_publishes_nothing(self, group_call) -> None:
        """UNJUDGED: the member was never asked, so the group recorded nothing (#1409)."""
        group = _group()

        result, bus = group_call(group, invoke=RateLimitExceeded(limit=1, window_seconds=1))

        assert result.success is False
        assert bus.local == [] and bus.appended == []
        assert group.get_member(_MEMBER).in_rotation is True

    def test_the_next_crud_command_republishes_none_of_it(self, group_call) -> None:
        """No event twice: the drain took each one, so the CRUD handler finds only its own."""
        group = _group()
        _result, bus = group_call(group, invoke=RuntimeError("backend down"))
        heard_on_the_call = list(bus.local)

        UpdateGroupHandler(groups={_GROUP: group}, event_bus=bus).handle(
            UpdateGroupCommand(group_id=_GROUP, description="edited")
        )

        assert bus.local == heard_on_the_call
        assert bus.appended_types == [GroupUpdated]


# ----------------------------------------------------------------------------
# The health-check path: the saga drains after it reports the check
# ----------------------------------------------------------------------------


class TestTheHealthCheckPathPublishesWhatItRaised:
    def test_a_passing_check_that_returns_a_member_and_closes_the_circuit_publishes_now(self) -> None:
        group = _tripped()
        bus = _RecordingBus()
        saga = GroupRebalanceSaga(groups={_GROUP: group}, event_bus=bus)

        saga.handle(HealthCheckPassed(mcp_server_id=_MEMBER, duration_ms=1.0))

        assert group.circuit_open is False
        assert GroupCircuitClosed in bus.local_types
        [back] = [e for e in bus.local if isinstance(e, GroupMemberHealthChanged)]
        assert back.member_id == _MEMBER and back.in_rotation is True
        assert bus.appended == []
        assert group.has_uncommitted_events() is False

    def test_a_check_that_changes_nothing_publishes_nothing(self) -> None:
        group = _group()
        bus = _RecordingBus()
        saga = GroupRebalanceSaga(groups={_GROUP: group}, event_bus=bus)

        saga.handle(HealthCheckPassed(mcp_server_id=_MEMBER, duration_ms=1.0))

        assert bus.local == [] and bus.appended == []

    def test_a_member_of_two_groups_drains_both(self) -> None:
        first, second = _tripped(), _tripped()
        bus = _RecordingBus()
        saga = GroupRebalanceSaga(groups={"east": first, "west": second}, event_bus=bus)

        saga.handle(HealthCheckPassed(mcp_server_id=_MEMBER, duration_ms=1.0))

        assert bus.local_types.count(GroupCircuitClosed) == 2
        assert first.has_uncommitted_events() is False and second.has_uncommitted_events() is False

    def test_without_a_bus_the_saga_reports_and_leaves_the_events_where_they_were(self) -> None:
        """The default. `bootstrap()` passes one; a saga built by hand need not."""
        group = _tripped()
        saga = GroupRebalanceSaga(groups={_GROUP: group})

        saga.handle(HealthCheckPassed(mcp_server_id=_MEMBER, duration_ms=1.0))

        assert group.circuit_open is False
        assert group.has_uncommitted_events() is True


# ----------------------------------------------------------------------------
# Rebalance
# ----------------------------------------------------------------------------


class TestRebalancePublishesWhatItRaised:
    def test_the_tool_drains_the_group_it_rebalanced(self) -> None:
        group = _tripped()
        bus = _RecordingBus()
        ctx = Mock()
        ctx.event_bus = bus
        ctx.group_exists.return_value = True
        ctx.get_group.return_value = group
        ctx.rate_limiter.consume.return_value = Mock(allowed=True)

        mcp = FastMCP("test")
        register_group_tools(mcp)
        with (
            patch("mcp_hangar.server.tools.groups.get_context", return_value=ctx),
            patch("mcp_hangar.server.validation.get_context", return_value=ctx),
        ):
            result = asyncio.run(mcp.call_tool("hangar_group_rebalance", {"group": _GROUP}))

        assert result.is_error is False, result.content
        assert GroupCircuitClosed in bus.local_types
        assert bus.appended == []
        assert group.has_uncommitted_events() is False


# ----------------------------------------------------------------------------
# Where the events go: local delivery, and no row in the shared log
# ----------------------------------------------------------------------------


class TestAgainstARealBusAndStore:
    """`publish_local` against the real bus: delivered here, and no stream written."""

    @staticmethod
    def _bus() -> tuple[EventBus, InMemoryEventStore, list[DomainEvent]]:
        store = InMemoryEventStore()
        bus = EventBus(event_store=store)
        heard: list[DomainEvent] = []
        bus.subscribe_to_all(heard.append, kind=HandlerKind.EFFECT)
        return bus, store, heard

    @staticmethod
    def _streams(store: InMemoryEventStore) -> list[str]:
        return [stream_id for _position, stream_id, _event in store.read_all(from_position=0)]

    def test_a_health_drain_is_delivered_here(self) -> None:
        bus, _store, heard = self._bus()
        group = _group()
        group.report_failure(_MEMBER)

        publish_group_events(bus, group)

        assert GroupCircuitOpened in [type(e) for e in heard]
        assert GroupMemberHealthChanged in [type(e) for e in heard]

    def test_and_writes_no_row_to_the_groups_stream(self) -> None:
        bus, store, _heard = self._bus()
        group = _group()
        group.report_failure(_MEMBER)

        publish_group_events(bus, group)

        assert self._streams(store) == []

    def test_while_the_groups_configuration_still_goes_to_its_stream(self) -> None:
        """The other half of the split: a CRUD command writes the group's history."""
        bus, store, _heard = self._bus()

        CreateGroupHandler(groups={}, event_bus=bus).handle(CreateGroupCommand(group_id=_GROUP))

        assert self._streams(store) == [stream_id_for(MCP_SERVER_GROUP, _GROUP)]


# ----------------------------------------------------------------------------
# Every event a group records is sorted on purpose
# ----------------------------------------------------------------------------


def _events_the_group_records() -> set[str]:
    """The class name of every event `McpServerGroup` hands to `_record_event`."""
    recorded: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(group_module))):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if callee != "_record_event" or not node.args:
            continue
        recorded |= {arg.func.id for arg in node.args if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)}
    return recorded


class TestEveryGroupEventIsSortedOnPurpose:
    def test_the_two_sets_do_not_overlap(self) -> None:
        assert not REPLICA_LOCAL_GROUP_EVENTS & SHARED_GROUP_EVENTS

    def test_every_event_a_group_records_is_in_one_of_them(self) -> None:
        # A new group event goes into REPLICA_LOCAL_GROUP_EVENTS if it describes
        # this replica's view -- rotation, the breaker, the state that follows --
        # or SHARED_GROUP_EVENTS if it describes the group's configuration.
        # Left out of both it would be treated as configuration and written to
        # the shared log, which is the decision #1358 defers.
        sorted_names = {cls.__name__ for cls in REPLICA_LOCAL_GROUP_EVENTS | SHARED_GROUP_EVENTS}

        assert _events_the_group_records() - sorted_names == set()

    def test_the_group_records_something_under_each_of_them(self) -> None:
        """Guards the guard: a walk that found nothing would pass the test above."""
        recorded = _events_the_group_records()

        assert recorded & {cls.__name__ for cls in REPLICA_LOCAL_GROUP_EVENTS}
        assert recorded & {cls.__name__ for cls in SHARED_GROUP_EVENTS}
