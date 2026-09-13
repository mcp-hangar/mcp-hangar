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
from mcp_hangar.domain.events import HealthCheckPassed, McpServerStarted, McpServerStateChanged
from mcp_hangar.domain.exceptions import McpServerStartError
from mcp_hangar.domain.model.mcp_server import DEAD_GIVEN_UP, McpServer
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
    repository = InMemoryMcpServerRepository()
    repository.add(fleet.sid, fleet.server)

    DeleteMcpServerHandler(repository, fleet.events).handle(DeleteMcpServerCommand(mcp_server_id=fleet.sid))

    assert _gone(fleet)


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
    loader = SimpleNamespace(load_from_file=lambda _path: {"mcp_servers": {}}, apply_mcp_servers=lambda _config: None)

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

    assert (server.state, server.given_up_snapshot) == (DEAD, True)


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
