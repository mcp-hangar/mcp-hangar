"""What starts a DEAD server again, what does not, and what it leaves behind (#1361).

Why a server died decides what may start it again. A member Hangar gave up on
is never chosen by a group; a member whose process crashed is, as it always was,
and the call restarts it. A call to any dead server respects the server's
backoff, so a server whose upstream is still broken is not restarted on every
call. And a dead server removed from the fleet takes its gauges with it.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from mcp_hangar.application.commands import StartMcpServerCommand
from mcp_hangar.application.sagas import GroupRebalanceSaga
from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import HealthCheckPassed, McpServerDeregistered, McpServerStarted, McpServerStateChanged
from mcp_hangar.domain.exceptions import CannotStartMcpServerError, McpServerStartError
from mcp_hangar.domain.model.mcp_server import DEAD_CAPABILITY_BLOCKED, DEAD_CRASHED, DEAD_GIVEN_UP, McpServer
from mcp_hangar.infrastructure.observability.metrics_event_handler import remove_series_of_deregistered
from mcp_hangar.domain.model.mcp_server_group import GroupMemberHealthChanged, McpServerGroup
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects import McpServerState
from tests.unit.test_a_given_up_server_reads_dead import _Fleet, _pipeline, _scraped, _state_changes

DEAD = McpServerState.DEAD
STATE = "mcp_hangar_mcp_server_state"
LAST_HEALTHY = "mcp_hangar_mcp_server_last_healthy_timestamp_seconds"


def _crash(fleet: _Fleet) -> None:
    """The process dies between two requests, and the health worker notices."""
    fleet.upstreams[-1].alive = False
    assert fleet.server.health_check() is False
    fleet.publish()


# ----------------------------------------------------------------------------
# A call respects the server's backoff
# ----------------------------------------------------------------------------


def test_calls_to_a_dead_server_start_it_at_most_once_per_backoff_window():
    # Without the backoff, every call restarted a server whose upstream was
    # still broken: 50 calls, 50 launches.
    from mcp_hangar.server.tools.batch.executor import BatchExecutor

    fleet = _Fleet(max_consecutive_failures=3)
    saga = McpServerRecoverySaga(max_retries=0, saga_manager=MagicMock())

    def saga_on(event: Any) -> None:  # what SagaManager._handle_event does
        if saga.should_handle(event):
            for command in saga.handle(event):
                fleet.bus.send(command)

    fleet.events.subscribe_to_all(saga_on, kind=HandlerKind.EFFECT)
    fleet.degrade()  # health checks degrade it; with no retries the saga gives up
    assert fleet.server.state is DEAD

    launches: list[str] = []

    def broken() -> Any:
        launches.append("launch")
        raise OSError("connection refused")

    fleet.server._create_client = broken  # type: ignore[method-assign]
    executor = BatchExecutor()
    ctx = SimpleNamespace(command_bus=fleet.bus)

    def storm() -> Counter[str]:
        outcomes: Counter[str] = Counter()
        for _ in range(50):
            pipeline = _pipeline(ctx, "dead")
            pipeline.mcp_server_obj = fleet.server
            refusal = executor._gate_circuit_breaker(pipeline) or executor._gate_cold_start(pipeline)
            outcomes[refusal.error_type if refusal is not None else "passed"] += 1
        return outcomes

    inside_the_backoff = storm()
    fleet.server.health._last_failure_at = 0.0  # the window has passed
    after_it = storm()

    assert inside_the_backoff == {"CircuitBreakerOpen": 50}
    assert after_it == {"McpServerStartError": 1, "CircuitBreakerOpen": 49}
    assert launches == ["launch"], "one start per backoff window"
    assert fleet.server.state is DEAD, "the failed start ends dead again, given up on"


# ----------------------------------------------------------------------------
# A group: crashed members are restarted, given-up ones are not chosen
# ----------------------------------------------------------------------------


def _group(*fleets: _Fleet) -> McpServerGroup:
    """A group whose members are in rotation, fed by the real GroupRebalanceSaga."""
    group = McpServerGroup(f"pool-{uuid4().hex[:8]}", auto_start=False, unhealthy_threshold=100)
    for fleet in fleets:
        group.add_member(fleet.server)
        group.get_member(fleet.sid).in_rotation = True
    saga = GroupRebalanceSaga(groups={group.id: group})
    for fleet in fleets:
        fleet.events.subscribe_to_all(saga.handle, kind=HandlerKind.EFFECT)
    return group


class TestACrashedMember:
    def test_stays_in_rotation_but_is_not_counted_healthy(self):
        fleet = _Fleet()
        fleet.start()
        group = _group(fleet)

        _crash(fleet)

        assert group.get_member(fleet.sid).in_rotation is True
        assert group.healthy_count == 0
        assert group.state.value == "inactive"

    def test_is_selected_and_restarted_by_a_call_through_the_group(self):
        # As before `dead` was visible. Refusing it left a one-member group down
        # until an operator acted.
        fleet = _Fleet()
        fleet.start()
        group = _group(fleet)
        _crash(fleet)

        selected = group.select_member()
        result = selected.invoke_tool("add", {"a": 1, "b": 2})
        fleet.publish()

        assert selected is fleet.server
        assert result == {"content": [{"type": "text", "text": "3"}]}
        assert (fleet.server.state, group.healthy_count) == (McpServerState.READY, 1)
        # The group's own state follows: it read inactive while the member was dead.
        assert group.state.value == "healthy"


class TestAGivenUpMember:
    def test_leaves_rotation_and_is_not_selected(self):
        fleet = _Fleet()
        group = _group(fleet)

        fleet.dead()

        assert group.get_member(fleet.sid).in_rotation is False
        assert group.healthy_count == 0
        assert group.select_member() is None
        reasons = [e.reason for e in group.collect_events() if isinstance(e, GroupMemberHealthChanged)]
        assert "given_up" in reasons

    def test_comes_back_on_an_explicit_start(self):
        fleet = _Fleet()
        group = _group(fleet)
        fleet.dead()

        fleet.bus.send(StartMcpServerCommand(mcp_server_id=fleet.sid))

        assert group.get_member(fleet.sid).in_rotation is True
        assert group.select_member() is fleet.server
        assert group.healthy_count == 1


# ----------------------------------------------------------------------------
# Removed from the fleet: its gauges go too
# ----------------------------------------------------------------------------


def _dead_on_the_metrics() -> _Fleet:
    fleet = _Fleet()
    fleet.dead()
    assert _scraped(STATE, fleet.sid) == 4.0
    assert _scraped(LAST_HEALTHY, fleet.sid) is not None
    return fleet


def _gone(fleet: _Fleet) -> bool:
    return _scraped(STATE, fleet.sid) is None and _scraped(LAST_HEALTHY, fleet.sid) is None


def test_deleting_a_dead_server_removes_its_series():
    from mcp_hangar.application.commands.crud_commands import DeleteMcpServerCommand
    from mcp_hangar.application.commands.crud_handlers import DeleteMcpServerHandler

    fleet = _dead_on_the_metrics()
    # As bootstrap subscribes it: a projection, so it runs on every replica.
    fleet.events.subscribe(McpServerDeregistered, remove_series_of_deregistered, kind=HandlerKind.PROJECTION)
    repository = InMemoryMcpServerRepository()
    repository.add(fleet.sid, fleet.server)

    DeleteMcpServerHandler(repository, fleet.events).handle(DeleteMcpServerCommand(mcp_server_id=fleet.sid))

    assert _gone(fleet)


def test_a_server_deleted_on_another_replica_loses_its_gauges_here():
    # The metrics handler is an effect, and the tailer delivers a peer's events
    # to projections only.
    from mcp_hangar.infrastructure.event_bus import EventBus

    fleet = _dead_on_the_metrics()
    peer = EventBus()
    peer.subscribe(McpServerDeregistered, remove_series_of_deregistered, kind=HandlerKind.PROJECTION)

    peer.deliver_tailed(McpServerDeregistered(mcp_server_id=fleet.sid, source="api"))

    assert _gone(fleet)


def test_bootstrap_subscribes_the_removal_as_a_projection():
    import inspect

    from mcp_hangar.server.bootstrap.event_handlers import init_event_handlers

    [line] = [
        line
        for line in inspect.getsource(init_event_handlers).splitlines()
        if "remove_series_of_deregistered" in line and "subscribe" in line
    ]
    assert "McpServerDeregistered" in line and "HandlerKind.PROJECTION" in line


def test_deleting_a_dead_server_closes_a_connection_it_still_holds():
    from mcp_hangar.application.commands.crud_commands import DeleteMcpServerCommand
    from mcp_hangar.application.commands.crud_handlers import DeleteMcpServerHandler

    fleet = _Fleet()
    fleet.start()
    fleet.publish()
    with fleet.server._lock:
        fleet.server._mark_dead(DEAD_CRASHED)  # dead, its connection not yet closed
    repository = InMemoryMcpServerRepository()
    repository.add(fleet.sid, fleet.server)

    DeleteMcpServerHandler(repository, fleet.events).handle(DeleteMcpServerCommand(mcp_server_id=fleet.sid))

    assert fleet.upstreams[-1].closed is True
    assert fleet.server.state is McpServerState.COLD


def test_unloading_a_dead_server_removes_its_series():
    from mcp_hangar.application.commands import UnloadMcpServerCommand
    from mcp_hangar.application.commands.load_handlers import UnloadMcpServerHandler
    from mcp_hangar.infrastructure.runtime_store import LoadMetadata, RuntimeMcpServerStore

    fleet = _dead_on_the_metrics()
    store = RuntimeMcpServerStore()
    store.add(fleet.server, LoadMetadata(loaded_at=datetime.now(), loaded_by=None, source="registry:x", verified=True))

    UnloadMcpServerHandler(store, fleet.events).handle(UnloadMcpServerCommand(mcp_server_id=fleet.sid))

    assert _gone(fleet)


def test_reloading_a_dead_server_away_removes_its_series():
    from mcp_hangar.application.commands import ReloadConfigurationCommand
    from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler

    fleet = _dead_on_the_metrics()
    repository = InMemoryMcpServerRepository()
    repository.add(fleet.sid, fleet.server)
    loader = SimpleNamespace(
        load_from_file=lambda _path: {"mcp_servers": {}},
        check_process_config=lambda _config: None,
        apply_process_config=lambda _config: None,
        prepare_mcp_servers=lambda _config: SimpleNamespace(specs={}, keeps=lambda *_: False),
        commit_mcp_servers=lambda _prepared: None,
    )

    ReloadConfigurationHandler(repository, fleet.events, "config.yaml", config_loader=loader).handle(
        ReloadConfigurationCommand(config_path="config.yaml")
    )

    assert _gone(fleet)


# ----------------------------------------------------------------------------
# Boot, with a durable event store
# ----------------------------------------------------------------------------


def _restored(events: list[Any], sid: str | None = None) -> McpServer:
    server = McpServer(mcp_server_id=sid or f"svc-{uuid4().hex[:12]}", mode="subprocess", command=["unused"])
    server.restore_from_events(events)
    return server


def _history(sid: str) -> list[Any]:
    return [
        McpServerStarted(
            mcp_server_id=sid, mode="subprocess", tools_count=1, startup_duration_ms=1.0, occurred_at=900.0
        ),
        HealthCheckPassed(mcp_server_id=sid, duration_ms=1.0, occurred_at=1000.0),
        McpServerStateChanged(mcp_server_id=sid, old_state="degraded", new_state="dead", dead_reason=DEAD_GIVEN_UP),
    ]


def test_a_restored_server_remembers_it_was_given_up_on():
    sid = f"svc-{uuid4().hex[:12]}"

    server = _restored(_history(sid), sid)

    assert (server.state, server.dead_reason_snapshot) == (DEAD, DEAD_GIVEN_UP)


def test_why_a_server_died_survives_the_event_store():
    from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer

    serializer = EventSerializer()
    event = McpServerStateChanged(
        mcp_server_id="svc", old_state="degraded", new_state="dead", dead_reason=DEAD_GIVEN_UP
    )

    assert serializer.deserialize(*serializer.serialize(event)).dead_reason == DEAD_GIVEN_UP


def test_a_state_change_stored_before_the_reason_existed_still_reads():
    import json

    from mcp_hangar.infrastructure.persistence.event_serializer import EventSerializer

    serializer = EventSerializer()
    event_type, data = serializer.serialize(
        McpServerStateChanged(mcp_server_id="svc", old_state="ready", new_state="dead")
    )
    payload = json.loads(data)
    payload.pop("dead_reason")

    restored = serializer.deserialize(event_type, json.dumps(payload))

    assert (restored.new_state, restored.dead_reason) == ("dead", None)


def test_a_restored_dead_server_is_put_on_its_gauges():
    # Replay publishes nothing, and nothing happens to a dead server afterwards,
    # so without this it had no state series at all.
    from mcp_hangar.server.bootstrap.persistence import _seed_lifecycle_metrics

    dead_sid, cold_sid = f"svc-{uuid4().hex[:12]}", f"svc-{uuid4().hex[:12]}"
    repository = InMemoryMcpServerRepository()
    repository.add(dead_sid, _restored(_history(dead_sid), dead_sid))
    repository.add(cold_sid, _restored([], cold_sid))

    _seed_lifecycle_metrics(SimpleNamespace(repository=repository), [dead_sid, cold_sid])

    assert (_scraped(STATE, dead_sid), _scraped(LAST_HEALTHY, dead_sid)) == (4.0, 1000.0)
    # A cold one gets nothing, as in any process before its first start.
    assert _scraped(STATE, cold_sid) is None


def test_a_restored_ready_server_is_not_put_on_its_gauges_as_up():
    # It has no process in this one. Seeded, it would read `up` 1 until a health
    # check found out, or for good with health checks off.
    from mcp_hangar.server.bootstrap.persistence import _seed_lifecycle_metrics

    sid = f"svc-{uuid4().hex[:12]}"
    repository = InMemoryMcpServerRepository()
    repository.add(sid, _restored([_history(sid)[0]], sid))
    assert repository.get(sid).state is McpServerState.READY

    _seed_lifecycle_metrics(SimpleNamespace(repository=repository), [sid])

    assert _scraped("mcp_hangar_mcp_server_up", sid) is None
    assert _scraped(STATE, sid) is None


def test_the_front_door_warm_up_skips_a_dead_server(monkeypatch):
    from mcp_hangar.server import lifecycle

    monkeypatch.setattr("mcp_hangar.domain.services.tool_access_resolver.is_front_door", lambda: True)
    servers = {
        "dead-one": SimpleNamespace(state=SimpleNamespace(value="dead")),
        "cold-one": SimpleNamespace(state=SimpleNamespace(value="cold")),
    }
    sent: list[str] = []
    runtime = SimpleNamespace(
        repository=SimpleNamespace(get_all_ids=lambda: list(servers), get=servers.get),
        command_bus=SimpleNamespace(send=lambda command: sent.append(command.mcp_server_id)),
    )

    lifecycle.warm_the_front_door_catalogue(runtime)

    assert sent == ["cold-one"]


def test_ready_read_back_with_no_connection_is_cold_not_crashed():
    # A server READY when the previous process died comes back READY with no
    # connection. That is not a crash of anything in this process.
    sid = f"svc-{uuid4().hex[:12]}"
    server = _restored([_history(sid)[0]], sid)
    assert server.state is McpServerState.READY

    assert server.health_check() is False

    assert server.state is McpServerState.COLD
    assert _state_changes(server.collect_events()) == [("ready", "cold")]


# ----------------------------------------------------------------------------
# The smaller ones
# ----------------------------------------------------------------------------


def test_a_stop_during_a_start_stays_cold():
    # Reading DEAD would page someone for an operator's own action.
    fleet = _Fleet()

    def launch_while_being_stopped() -> Any:
        fleet.server.shutdown()
        raise OSError("the process was killed by the stop")

    fleet.server._create_client = launch_while_being_stopped  # type: ignore[method-assign]

    with pytest.raises(McpServerStartError):
        fleet.server.ensure_ready()

    assert fleet.server.state is McpServerState.COLD
    assert ("initializing", "dead") not in _state_changes(fleet.server.collect_events())


def test_a_crash_closes_what_is_left_of_the_connection():
    fleet = _Fleet()
    fleet.start()

    _crash(fleet)

    assert fleet.upstreams[-1].closed is True


def test_giving_up_clears_the_tool_catalogue():
    # A stop always did; a dead server listed the tools it no longer serves.
    fleet = _Fleet()
    fleet.degrade()
    assert fleet.server.get_tool_names() == ["add"]

    fleet.give_up()

    assert fleet.server.get_tool_names() == []


# ----------------------------------------------------------------------------
# A capability block: only a deliberate start revives it
# ----------------------------------------------------------------------------


def _block(fleet: _Fleet) -> None:
    """Stopped by a capability block, as block mode stops a server whose tools drifted."""
    with fleet.server._lock:
        fleet.server._mark_dead(DEAD_CAPABILITY_BLOCKED)
    fleet.publish()


class TestACapabilityBlock:
    def test_is_not_revived_by_a_call(self):
        fleet = _Fleet()
        fleet.start()
        _block(fleet)

        with pytest.raises(CannotStartMcpServerError, match="not revived by a call"):
            fleet.server.invoke_tool("add", {"a": 1, "b": 2})

        assert fleet.server.state is DEAD

    def test_is_revived_by_a_deliberate_start(self):
        fleet = _Fleet()
        fleet.start()
        _block(fleet)

        fleet.bus.send(StartMcpServerCommand(mcp_server_id=fleet.sid))

        assert fleet.server.state is McpServerState.READY

    def test_is_not_routed_to_by_a_group_and_leaves_rotation(self):
        blocked, cold = _Fleet(), _Fleet()
        blocked.start()
        group = _group(blocked, cold)

        _block(blocked)

        assert group.get_member(blocked.sid).in_rotation is False
        assert {group.select_member().mcp_server_id for _ in range(4)} == {cold.sid}

    def test_the_batch_refuses_a_call_to_it(self):
        from mcp_hangar.server.tools.batch.executor import BatchExecutor

        pipeline = _pipeline(MagicMock(), "dead")
        pipeline.mcp_server_obj.dead_reason_snapshot = DEAD_CAPABILITY_BLOCKED

        refusal = BatchExecutor()._gate_circuit_breaker(pipeline)

        assert refusal is not None and refusal.error_type == "CannotStartMcpServerError"


# ----------------------------------------------------------------------------
# A group member the batch refuses counts as its failure, so the group fails over
# ----------------------------------------------------------------------------


class TestAGroupMemberTheBatchRefuses:
    """As a failed invocation does. Otherwise a member whose restart keeps
    failing stays in rotation, keeps drawing calls, and nothing fails over."""

    @staticmethod
    def _member(state: str, **health: bool) -> tuple[Any, Any]:
        from mcp_hangar.server.tools.batch.executor import BatchExecutor

        pipeline = _pipeline(MagicMock(), state, **health)
        pipeline.is_group, pipeline.group_obj = True, MagicMock()
        return BatchExecutor(), pipeline

    def test_a_dead_member_in_its_backoff(self):
        executor, pipeline = self._member("dead", should_degrade=True, can_retry=False)

        assert executor._gate_circuit_breaker(pipeline).error_type == "CircuitBreakerOpen"
        pipeline.group_obj.report_failure.assert_called_once_with("svc")

    def test_a_degraded_member(self):
        executor, pipeline = self._member("degraded", should_degrade=True)

        assert executor._gate_circuit_breaker(pipeline).error_type == "CircuitBreakerOpen"
        pipeline.group_obj.report_failure.assert_called_once_with("svc")

    def test_a_member_whose_start_fails(self):
        executor, pipeline = self._member("dead")
        pipeline.ctx.command_bus.send.side_effect = McpServerStartError("svc", "the process exited")

        assert executor._gate_cold_start(pipeline).error_type == "McpServerStartError"
        pipeline.group_obj.report_failure.assert_called_once_with("svc")

    def test_a_member_whose_start_succeeds_is_not_counted(self):
        executor, pipeline = self._member("dead")

        assert executor._gate_cold_start(pipeline) is None
        pipeline.group_obj.report_failure.assert_not_called()


def test_the_cold_start_judges_a_dead_target_again():
    # The approval gate can hold a call for minutes after the circuit-breaker
    # gate let it through; the backoff may have started since.
    from mcp_hangar.server.tools.batch.executor import BatchExecutor

    pipeline = _pipeline(MagicMock(), "dead", can_retry=False)

    assert BatchExecutor()._gate_cold_start(pipeline).error_type == "CircuitBreakerOpen"
    pipeline.ctx.command_bus.send.assert_not_called()


# ----------------------------------------------------------------------------
# Listing and warming are not deliberate starts
# ----------------------------------------------------------------------------


def _context(**fields: Any) -> tuple[SimpleNamespace, list[str]]:
    sent: list[str] = []
    return SimpleNamespace(command_bus=SimpleNamespace(send=lambda c: sent.append(c.mcp_server_id)), **fields), sent


class TestHangarTools:
    def test_does_not_start_a_dead_server(self, monkeypatch):
        # A given-up server's catalogue is cleared, and listing it used to start
        # it, inside its backoff.
        from mcp_hangar.server.tools import mcp_server as tools

        fleet = _Fleet()
        fleet.dead()
        ctx, sent = _context(get_mcp_server=lambda _id: fleet.server)
        monkeypatch.setattr(tools, "get_context", lambda: ctx)

        result = tools._get_tools_for_mcp_server(fleet.sid)

        assert result == {"mcp_server": fleet.sid, "state": "dead", "predefined": False, "tools": []}
        assert sent == [] and fleet.server.state is DEAD

    def test_does_not_start_a_dead_group_member(self, monkeypatch):
        from mcp_hangar.server.tools import mcp_server as tools

        fleet = _Fleet()
        fleet.start()
        group = _group(fleet)
        _crash(fleet)
        ctx, sent = _context(get_group=lambda _id: group)
        monkeypatch.setattr(tools, "get_context", lambda: ctx)

        result = tools._get_tools_for_group("pool")

        assert result == {"mcp_server": "pool", "group": True, "state": "dead", "tools": []}
        assert sent == []


def test_hangar_warm_of_everything_skips_a_dead_server_and_naming_it_starts_it(monkeypatch):
    from mcp_hangar.server.tools import mcp_server as tools

    registered: dict[str, Any] = {}

    class _Mcp:
        def tool(self, name: str | None = None, **_kwargs: Any) -> Any:
            def register(fn: Any) -> Any:
                registered[name or fn.__name__] = fn
                return fn

            return register

    tools.register_mcp_server_tools(_Mcp())
    dead, cold = _Fleet(), _Fleet()
    dead.dead()
    servers = {dead.sid: dead.server, cold.sid: cold.server}
    ctx, sent = _context(
        repository=SimpleNamespace(get_all=lambda: dict(servers)),
        group_exists=lambda _id: False,
        mcp_server_exists=servers.__contains__,
        get_mcp_server=servers.get,
    )
    monkeypatch.setattr(tools, "get_context", lambda: ctx)

    everything = registered["hangar_warm"]()
    named = registered["hangar_warm"](mcp_servers=dead.sid)

    assert (everything["warmed"], everything["skipped_dead"]) == ([cold.sid], [dead.sid])
    assert named["warmed"] == [dead.sid], "naming a dead server is a deliberate start"
    assert sent == [cold.sid, dead.sid]
