"""A server Hangar gives up on reads dead, stays dead, and only a deliberate action revives it (#1361, #1359).

DEAD has one meaning: the server failed and nothing automatic will start it
again. Three failure paths land there -- a crashed process, a failed start
below the degrade threshold, and the recovery saga running out of retries --
and two ways lead out: an explicit start and a call.

Before this, the saga's give-up was a stop, so a server Hangar had given up on
read `cold`, the state of one nobody has called. The other two paths did reach
DEAD, but by assignment, with no event, so `mcp_hangar_mcp_server_state` kept
whatever an event had last set: `ready` for a crashed process.

The aggregate here is the real one. Only its launcher is replaced, by a
connection whose `tools/list` can be made to fail, which is what an upstream
going bad looks like to a health check.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import pytest

from mcp_hangar import metrics as m
from mcp_hangar.application.commands import GiveUpOnMcpServerCommand, StartMcpServerCommand
from mcp_hangar.application.commands.handlers import GiveUpOnMcpServerHandler, StartMcpServerHandler
from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import (
    DomainEvent,
    HealthCheckPassed,
    McpServerDegraded,
    McpServerStarted,
    McpServerStateChanged,
    McpServerStopped,
    ToolInvocationCompleted,
)
from mcp_hangar.domain.exceptions import McpServerStartError
from mcp_hangar.domain.model.health_tracker import HealthTracker
from mcp_hangar.domain.model.mcp_server import VALID_TRANSITIONS, McpServer
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.gc import BackgroundWorker
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.observability.metrics_event_handler import MetricsEventHandler
from mcp_hangar.stream_ids import MCP_SERVER

DEAD = McpServerState.DEAD


class _Upstream:
    """A connection whose `tools/list` fails once `failing` is set."""

    def __init__(self) -> None:
        self.failing = False
        self.alive = True
        self.closed = False
        self.modern_envelope = False

    def is_alive(self) -> bool:
        return self.alive and not self.closed

    def call(self, method: str, params: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        if method == "initialize":
            return {"result": {"protocolVersion": "2025-11-25"}}
        if method == "tools/list":
            if self.failing:
                return {"error": {"code": -32603, "message": "upstream is down"}}
            return {"result": {"tools": [{"name": "add"}]}}
        if method == "tools/call":
            return {"result": {"content": [{"type": "text", "text": "3"}]}}
        return {"error": {"code": -32601, "message": method}}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _Fleet:
    """One server, every connection it launched, and a bus that feeds /metrics."""

    def __init__(self, max_consecutive_failures: int = 2) -> None:
        self.upstreams: list[_Upstream] = []
        self.server = McpServer(
            mcp_server_id=f"svc-{uuid4().hex[:12]}",
            mode="subprocess",
            command=["unused"],
            max_consecutive_failures=max_consecutive_failures,
            metrics_publisher=Mock(),
        )
        self.server._create_client = self._launch  # type: ignore[method-assign]
        self.events = EventBus()
        self.events.subscribe_to_all(MetricsEventHandler().handle, kind=HandlerKind.EFFECT)
        self.published: list[DomainEvent] = []
        self.events.subscribe_to_all(self.published.append, kind=HandlerKind.EFFECT)
        repository = Mock(get=Mock(return_value=self.server))
        self.bus = CommandBus()
        self.bus.register(StartMcpServerCommand, StartMcpServerHandler(repository, self.events))
        self.bus.register(GiveUpOnMcpServerCommand, GiveUpOnMcpServerHandler(repository, self.events))

    @property
    def sid(self) -> str:
        return self.server.mcp_server_id

    def _launch(self) -> _Upstream:
        upstream = _Upstream()
        self.upstreams.append(upstream)
        return upstream

    def publish(self) -> list[DomainEvent]:
        """What the handlers and workers do after touching the aggregate."""
        events = list(self.server.collect_events())
        if events:
            self.events.publish_aggregate_events(MCP_SERVER, self.sid, events)
        return events

    def start(self) -> None:
        self.bus.send(StartMcpServerCommand(mcp_server_id=self.sid))

    def degrade(self) -> None:
        self.start()
        self.upstreams[-1].failing = True
        while self.server.state is not McpServerState.DEGRADED:
            self.server.health_check()
        self.publish()

    def give_up(self) -> dict[str, Any]:
        return self.bus.send(GiveUpOnMcpServerCommand(mcp_server_id=self.sid, reason="max_retries_exceeded"))

    def dead(self) -> None:
        self.degrade()
        self.give_up()
        assert self.server.state is DEAD


def _gauge(metric: Any, sid: str, **labels: str) -> float | None:
    for sample in metric.collect():
        if sample.labels.get("mcp_server") == sid and all(sample.labels.get(k) == v for k, v in labels.items()):
            return float(sample.value)
    return None


def _scraped(name: str, sid: str) -> float | None:
    """The sample for `sid` in the exposition /metrics serves, or None."""
    prefix = f'{name}{{mcp_server="{sid}"}} '
    for line in m.get_metrics().splitlines():
        if line.startswith(prefix):
            return float(line.split()[-1])
    return None


def _state_changes(events: list[DomainEvent]) -> list[tuple[str, str]]:
    return [(e.old_state, e.new_state) for e in events if isinstance(e, McpServerStateChanged)]


# ----------------------------------------------------------------------------
# The transitions into DEAD
# ----------------------------------------------------------------------------


class TestGivingUp:
    def test_leaves_a_degraded_server_dead_and_closes_its_connection(self):
        fleet = _Fleet()
        fleet.degrade()

        assert fleet.server.give_up("max_retries_exceeded") is True

        assert fleet.server.state is DEAD
        assert fleet.upstreams[-1].closed
        events = fleet.publish()
        assert _state_changes(events) == [("degraded", "dead")]
        assert not [e for e in events if isinstance(e, McpServerStopped)], "a give-up is not a stop: stops read cold"

    @pytest.mark.parametrize("state", ["cold", "ready", "dead"])
    def test_leaves_any_other_state_alone(self, state):
        # Anything but DEGRADED means the server moved on after the event the
        # saga acted on, and that stands.
        fleet = _Fleet()
        if state == "ready":
            fleet.start()
        elif state == "dead":
            fleet.dead()
        fleet.publish()

        assert fleet.server.give_up("max_retries_exceeded") is False

        assert fleet.server.state.value == state
        assert fleet.publish() == []

    def test_through_the_command_bus_reaches_the_state_gauge(self):
        fleet = _Fleet()
        fleet.degrade()
        assert _gauge(m.PROVIDER_STATE_CURRENT, fleet.sid) == 3.0

        result = fleet.give_up()

        assert result == {"mcp_server": fleet.sid, "gave_up": True, "state": "dead"}
        assert _scraped("mcp_hangar_mcp_server_state", fleet.sid) == 4.0
        assert _scraped("mcp_hangar_mcp_server_up", fleet.sid) == 0.0

    def test_keeps_the_stop_count_it_had_and_loses_the_false_one(self):
        # It was a stop: counted once under the saga's reason by the handler,
        # and once more as "shutdown" from the event the stop published.
        fleet = _Fleet()
        fleet.dead()

        assert _gauge(m.PROVIDER_STOPS_TOTAL, fleet.sid, reason="max_retries_exceeded") == 1.0
        assert _gauge(m.PROVIDER_STOPS_TOTAL, fleet.sid, reason="shutdown") is None


class TestTheOtherWaysIn:
    def test_a_crashed_process_reads_dead(self):
        fleet = _Fleet()
        fleet.start()
        fleet.publish()
        fleet.upstreams[-1].alive = False

        assert fleet.server.health_check() is False

        assert fleet.server.state is DEAD
        assert _state_changes(fleet.publish()) == [("ready", "dead")]
        # It read `ready` -- and `up` 1 -- for as long as nobody called it.
        assert _scraped("mcp_hangar_mcp_server_state", fleet.sid) == 4.0
        assert _scraped("mcp_hangar_mcp_server_up", fleet.sid) == 0.0

    def test_a_failed_start_below_the_degrade_threshold_reads_dead(self):
        fleet = _Fleet(max_consecutive_failures=3)
        fleet.server._create_client = Mock(side_effect=OSError("no such file"))  # type: ignore[method-assign]

        with pytest.raises(McpServerStartError):
            fleet.start()

        assert fleet.server.state is DEAD
        assert _state_changes(fleet.published) == [("cold", "initializing"), ("initializing", "dead")]
        # It read `initializing` from then on.
        assert _scraped("mcp_hangar_mcp_server_state", fleet.sid) == 4.0

    def test_the_transition_table_has_the_give_up_and_the_way_out(self):
        assert McpServerState.DEAD in VALID_TRANSITIONS[McpServerState.DEGRADED]
        assert McpServerState.INITIALIZING in VALID_TRANSITIONS[McpServerState.DEAD]


# ----------------------------------------------------------------------------
# Out of DEAD: a deliberate action, and nothing else
# ----------------------------------------------------------------------------


class TestTheWayOut:
    def test_an_explicit_start_revives_it(self):
        fleet = _Fleet()
        fleet.dead()
        fleet.published.clear()

        result = fleet.bus.send(StartMcpServerCommand(mcp_server_id=fleet.sid))

        assert result["state"] == "ready"
        assert _state_changes(fleet.published)[:2] == [("dead", "initializing"), ("initializing", "ready")]
        assert len(fleet.upstreams) == 2, "a new connection, not the closed one"
        assert _scraped("mcp_hangar_mcp_server_state", fleet.sid) == 2.0

    def test_a_call_revives_it(self):
        fleet = _Fleet()
        fleet.dead()

        result = fleet.server.invoke_tool("add", {"a": 1, "b": 2})

        assert result == {"content": [{"type": "text", "text": "3"}]}
        assert fleet.server.state is McpServerState.READY

    def test_a_health_check_does_not(self):
        fleet = _Fleet()
        fleet.dead()

        assert fleet.server.health_check() is False

        assert fleet.server.state is DEAD
        assert fleet.publish() == []

    @pytest.mark.parametrize("task", ["health_check", "gc"])
    def test_the_background_workers_do_not(self, task):
        # One turn of each loop. The health worker used to call health_check()
        # on a DEAD server every 60s and count an `unhealthy` check that never
        # probed anything.
        fleet = _Fleet()
        fleet.dead()
        fleet.server.health_check = Mock(wraps=fleet.server.health_check)  # type: ignore[method-assign]
        worker = BackgroundWorker({fleet.sid: fleet.server}, interval_s=1, task=task, event_bus=fleet.events)
        worker.running = True
        checks_before = _gauge(m.HEALTH_CHECK_TOTAL, fleet.sid, result="unhealthy")

        with patch("mcp_hangar.gc.time.sleep", side_effect=[None, StopIteration]), pytest.raises(StopIteration):
            worker._loop()

        fleet.server.health_check.assert_not_called()
        assert fleet.server.state is DEAD
        assert _gauge(m.HEALTH_CHECK_TOTAL, fleet.sid, result="unhealthy") == checks_before

    def test_the_health_worker_schedules_no_check_for_it(self):
        assert HealthTracker().get_health_check_interval("dead") == 0.0


class TestTheRecoverySaga:
    def test_gives_up_instead_of_stopping(self):
        saga = McpServerRecoverySaga(max_retries=1, saga_manager=MagicMock())

        saga.handle(McpServerDegraded("svc", 3, 3, "error"))
        commands = saga.handle(McpServerDegraded("svc", 4, 4, "error"))

        assert commands == [GiveUpOnMcpServerCommand(mcp_server_id="svc", reason="max_retries_exceeded")]

    def test_cancels_the_restarts_it_still_has_waiting(self):
        # A restart that fires after the give-up would start the server again:
        # the saga reviving what it gave up on.
        manager = MagicMock()
        manager.schedule_command.side_effect = ["t1", "t2"]
        saga = McpServerRecoverySaga(max_retries=2, saga_manager=manager)

        for failures in (3, 4, 5):
            saga.handle(McpServerDegraded("svc", failures, failures, "error"))

        assert [c.args[0] for c in manager.cancel_scheduled_command.call_args_list] == ["t1", "t2"]

    def test_a_recovery_forgets_its_restarts(self):
        manager = MagicMock()
        manager.schedule_command.side_effect = ["t1", "t2"]
        saga = McpServerRecoverySaga(max_retries=1, saga_manager=manager)

        saga.handle(McpServerDegraded("svc", 3, 3, "error"))
        saga.handle(McpServerStarted("svc", "subprocess", 1, 1.0))
        saga.handle(McpServerDegraded("svc", 3, 3, "error"))
        saga.handle(McpServerDegraded("svc", 4, 4, "error"))

        assert [c.args[0] for c in manager.cancel_scheduled_command.call_args_list] == ["t2"]


class TestAGroup:
    """A group choosing a dead member would be the group reviving it."""

    @staticmethod
    def _group(*servers: McpServer) -> Any:
        from mcp_hangar.domain.model.mcp_server_group import McpServerGroup

        group = McpServerGroup(f"pool-{uuid4().hex[:8]}", auto_start=False)
        for server in servers:
            group.add_member(server)
            # In rotation, as a member is when it died between two health reports.
            group.get_member(server.mcp_server_id).in_rotation = True
        return group

    def test_does_not_select_a_dead_member_in_rotation(self):
        dead, cold = _Fleet(), _Fleet()
        dead.dead()

        group = self._group(dead.server, cold.server)

        assert {group.select_member().mcp_server_id for _ in range(6)} == {cold.sid}

    def test_with_only_dead_members_selects_none(self):
        dead = _Fleet()
        dead.dead()

        assert self._group(dead.server).select_member() is None

    def test_a_canary_pin_to_a_dead_member_falls_back(self):
        dead, cold = _Fleet(), _Fleet()
        dead.dead()
        group = self._group(dead.server, cold.server)
        group.set_canary_policy(Mock(resolve=Mock(return_value=dead.sid)))

        assert group.select_member_for("tenant-a").mcp_server_id == cold.sid


# ----------------------------------------------------------------------------
# The last-healthy timestamp (#1359)
# ----------------------------------------------------------------------------


def _last_healthy(sid: str) -> float | None:
    return _scraped("mcp_hangar_mcp_server_last_healthy_timestamp_seconds", sid)


def _occurred(events: list[DomainEvent], kind: type[DomainEvent]) -> float:
    [event] = [e for e in events if isinstance(e, kind)]
    return event.occurred_at


class TestLastHealthy:
    def test_is_absent_until_the_server_is_seen_working(self):
        assert _last_healthy(_Fleet().sid) is None

    def test_a_start_a_passing_check_and_a_call_each_write_when_it_happened(self):
        fleet = _Fleet()

        fleet.start()
        assert _last_healthy(fleet.sid) == _occurred(fleet.published, McpServerStarted)

        time.sleep(0.01)
        assert fleet.server.health_check() is True
        passed = _occurred(fleet.publish(), HealthCheckPassed)
        assert _last_healthy(fleet.sid) == passed

        time.sleep(0.01)
        fleet.server.invoke_tool("add", {"a": 1, "b": 2})
        completed = _occurred(fleet.publish(), ToolInvocationCompleted)
        assert _last_healthy(fleet.sid) == completed > passed

    def test_a_failing_check_does_not_touch_it(self):
        fleet = _Fleet(max_consecutive_failures=5)
        fleet.start()
        before = _last_healthy(fleet.sid)
        fleet.upstreams[-1].failing = True

        assert fleet.server.health_check() is False
        fleet.publish()

        assert _last_healthy(fleet.sid) == before

    def test_survives_the_server_going_cold(self):
        fleet = _Fleet()
        fleet.start()
        fleet.server.health_check()
        last = _occurred(fleet.publish(), HealthCheckPassed)

        fleet.server.shutdown()
        fleet.publish()

        assert _scraped("mcp_hangar_mcp_server_state", fleet.sid) == 0.0
        assert _last_healthy(fleet.sid) == last

    def test_survives_the_server_being_given_up_on(self):
        # The give-up path: the value is the last check that passed before the
        # upstream went bad, and it stays that while the server is dead.
        fleet = _Fleet()
        fleet.start()
        fleet.server.health_check()
        last = _occurred(fleet.publish(), HealthCheckPassed)

        fleet.upstreams[-1].failing = True
        while fleet.server.state is not McpServerState.DEGRADED:
            fleet.server.health_check()
        fleet.publish()
        fleet.give_up()

        assert _scraped("mcp_hangar_mcp_server_state", fleet.sid) == 4.0
        assert _last_healthy(fleet.sid) == last

    def test_never_moves_back(self):
        # Two events can be handled out of the order they happened in.
        sid = f"svc-{uuid4().hex[:12]}"

        m.record_mcp_server_healthy(sid, 2000.0)
        m.record_mcp_server_healthy(sid, 1000.0)

        assert _last_healthy(sid) == 2000.0


# ----------------------------------------------------------------------------
# A call through hangar_call starts a dead target like a cold one
# ----------------------------------------------------------------------------


def _pipeline(ctx: Any, state: str, should_degrade: bool = False) -> Any:
    from types import SimpleNamespace

    from mcp_hangar.observability.tracing import get_tracer
    from mcp_hangar.server.tools.batch.executor import _CallPipeline
    from mcp_hangar.server.tools.batch.models import CallSpec

    now = time.perf_counter()
    pipeline = _CallPipeline(
        call=CallSpec(index=0, call_id="c-1361", mcp_server="svc", tool="add", arguments={}),
        ctx=ctx,
        call_start=now,
        cancel_event=threading.Event(),
        global_timeout=30.0,
        batch_start_time=now,
        caller_tenant_id=None,
        resolver=None,
        proj_registry=None,
        tracer=get_tracer(__name__),
    )
    pipeline.mcp_server_obj = SimpleNamespace(
        state=SimpleNamespace(value=state),
        health=SimpleNamespace(should_degrade=lambda: should_degrade),
    )
    pipeline.target_server_id = "svc"
    return pipeline


@pytest.mark.parametrize("state", ["cold", "dead"])
def test_the_batch_starts_a_target_that_is_not_running(state):
    from mcp_hangar.server.tools.batch.executor import BatchExecutor

    ctx = MagicMock()

    assert BatchExecutor()._gate_cold_start(_pipeline(ctx, state)) is None

    [command] = [c.args[0] for c in ctx.command_bus.send.call_args_list]
    assert command == StartMcpServerCommand(mcp_server_id="svc")


def test_the_batch_circuit_breaker_lets_a_call_through_to_a_dead_target():
    # A dead server keeps the failure count that got it given up on, and this
    # gate refused every call to it -- so a call could never revive it.
    from mcp_hangar.server.tools.batch.executor import BatchExecutor

    assert BatchExecutor()._gate_circuit_breaker(_pipeline(MagicMock(), "dead", should_degrade=True)) is None


def test_the_batch_circuit_breaker_still_refuses_a_degraded_target():
    from mcp_hangar.server.tools.batch.executor import BatchExecutor

    refusal = BatchExecutor()._gate_circuit_breaker(_pipeline(MagicMock(), "degraded", should_degrade=True))

    assert refusal is not None and refusal.error_type == "CircuitBreakerOpen"
