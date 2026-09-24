"""A group whose members all left rotation and went COLD or DEAD recovers (#1565).

Real McpServerGroup, real GroupRebalanceSaga, real McpServer aggregates, and the
real worker loops -- GC, health check and the recovery probe -- each driven one
cycle at a time. The only fakes are the upstream client, a clock for the
probe's backoff, and a bus that hands every published event to the saga, which
is what bootstrap's event bus does.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from mcp_hangar.application.sagas import GroupRebalanceSaga
from mcp_hangar.domain.events import McpServerDegraded
from mcp_hangar.domain.model.mcp_server import DEAD_CAPABILITY_BLOCKED, DEAD_GIVEN_UP, McpServer
from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.gc import BackgroundWorker
from mcp_hangar.group_recovery import GroupRecoveryWorker

MEMBERS = ("a", "b")


class Upstream:
    def __init__(self) -> None:
        self.healthy = False
        self.connects: list[str] = []


class FakeClient:
    def __init__(self, upstream: Upstream) -> None:
        self._up = upstream

    def is_alive(self) -> bool:
        return True

    def call(self, method: str, params: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
        if not self._up.healthy:
            raise OSError("upstream down")
        return {"result": {"tools": []}}

    def close(self) -> None:
        pass


class SagaBus:
    """Every event to the group saga, as bootstrap's bus does.

    ``give_up_on_degrade`` stands in for a recovery saga whose retry budget is
    spent: a member that degrades is given up on at once.
    """

    def __init__(self, saga: GroupRebalanceSaga, servers: dict[str, McpServer]) -> None:
        self.saga = saga
        self.servers = servers
        self.give_up_on_degrade = False

    def publish_aggregate_events(self, aggregate_type, aggregate_id, events, expected_version=-1):
        for e in events:
            self.saga.handle(e)
            if self.give_up_on_degrade and isinstance(e, McpServerDegraded):
                server = self.servers[e.mcp_server_id]
                server.give_up(DEAD_GIVEN_UP)
                self.publish_aggregate_events("mcp_server", aggregate_id, list(server.collect_events()))
        return 0

    def publish(self, event):
        pass

    publish_local = publish


class OneShot:
    """Stands in for a worker's stop Event: lets the loop run exactly one cycle."""

    def __init__(self) -> None:
        self._n = 0

    def wait(self, _timeout: float) -> bool:
        self._n += 1
        return self._n > 1


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _cycle(worker: Any) -> None:
    worker.running = True
    worker._stopped = OneShot()
    if isinstance(worker, BackgroundWorker):
        worker._next_check_at.clear()  # every server due
    worker._loop()


class World:
    def __init__(self, *, stuck: bool = True) -> None:
        self.up = Upstream()
        self.servers = {
            m: McpServer(mcp_server_id=m, mode="subprocess", command=["echo"], idle_ttl_s=300) for m in MEMBERS
        }
        for m, s in self.servers.items():
            s._client = FakeClient(self.up)
            s._state = McpServerState.READY
            s._last_used = time.time()
            s._create_client = self._connect(m)  # type: ignore[method-assign]
        self.group = McpServerGroup(
            group_id="pool", auto_start=False, unhealthy_threshold=2, healthy_threshold=1, circuit_failure_threshold=4
        )
        for s in self.servers.values():
            self.group.add_member(s)
            self.group.get_member(s.mcp_server_id).in_rotation = True
        self.groups = {"pool": self.group}
        saga = GroupRebalanceSaga(groups=self.groups)
        self.bus = SagaBus(saga, self.servers)
        saga._event_bus = self.bus
        self.gc = BackgroundWorker(self.servers, interval_s=1, task="gc", event_bus=self.bus)
        self.hc = BackgroundWorker(self.servers, interval_s=1, task="health_check", event_bus=self.bus)
        self.clock = Clock()
        self.probe = GroupRecoveryWorker(self.groups, interval_s=1, event_bus=self.bus, clock=self.clock)
        assert self.group.select_member() is not None
        if stuck:
            # Call-path failures, exactly what executor._report_member does on UNHEALTHY.
            for _ in range(2):
                for m in MEMBERS:
                    self.group.report_failure(m)
            assert self.group.select_member() is None and self.group.circuit_open is True

    def _connect(self, member: str):
        def create_client() -> FakeClient:
            self.up.connects.append(member)
            return FakeClient(self.up)

        return create_client

    def reap_idle(self) -> None:
        for s in self.servers.values():
            s._last_used = time.time() - 301
        _cycle(self.gc)
        assert self.states() == {"a": "cold", "b": "cold"}

    def degrade_and_give_up(self) -> None:
        for _ in range(3):
            _cycle(self.hc)
        assert self.states() == {"a": "degraded", "b": "degraded"}
        for s in self.servers.values():
            assert s.give_up(DEAD_GIVEN_UP)
            self.bus.publish_aggregate_events("mcp_server", s.mcp_server_id, list(s.collect_events()))
        assert {s.dead_reason_snapshot for s in self.servers.values()} == {DEAD_GIVEN_UP}

    def all_workers_once(self) -> None:
        _cycle(self.gc)
        _cycle(self.hc)
        _cycle(self.probe)

    def states(self) -> dict[str, str]:
        return {m: s.state.value for m, s in self.servers.items()}


def test_idle_reaped_cold_members_are_started_again_once_the_upstream_is_back():
    w = World()
    w.reap_idle()
    w.up.healthy = True

    w.all_workers_once()

    assert w.states() == {"a": "ready", "b": "ready"}
    assert w.group.select_member() is not None
    assert w.group.circuit_open is False
    assert all(w.group.get_member(m).in_rotation for m in MEMBERS)


def test_given_up_dead_members_are_started_again_once_the_upstream_is_back():
    w = World()
    w.degrade_and_give_up()
    w.up.healthy = True

    w.all_workers_once()

    assert w.states() == {"a": "ready", "b": "ready"}
    assert w.group.select_member() is not None
    assert w.group.circuit_open is False


def test_a_group_with_a_selectable_member_is_not_touched():
    w = World(stuck=False)
    # "b" failed out of rotation and was reaped; "a" still serves.
    for _ in range(2):
        w.group.report_failure("b")
    w.servers["b"]._last_used = time.time() - 301
    _cycle(w.gc)
    assert w.states() == {"a": "ready", "b": "cold"}
    w.up.healthy = True

    for _ in range(5):
        _cycle(w.probe)
        w.clock.now += 3600

    assert w.up.connects == []
    assert w.servers["b"].state is McpServerState.COLD


def test_a_group_never_started_or_stopped_on_purpose_is_left_alone():
    w = World(stuck=False)
    w.up.healthy = True
    w.group.stop_all()
    lazy = McpServerGroup(group_id="lazy", auto_start=False)
    lazy.add_member(McpServer(mcp_server_id="c", mode="subprocess", command=["echo"]))
    w.groups["lazy"] = lazy
    assert w.group.select_member() is None and lazy.select_member() is None

    _cycle(w.probe)

    assert w.up.connects == []
    assert w.states() == {"a": "cold", "b": "cold"}


def test_a_capability_blocked_member_is_not_started():
    w = World()
    w.reap_idle()
    w.servers["a"]._state = McpServerState.DEAD
    w.servers["a"]._dead_reason = DEAD_CAPABILITY_BLOCKED
    w.up.healthy = True

    _cycle(w.probe)

    assert w.up.connects == ["b"]


@pytest.mark.parametrize("route", ["cold", "given_up"])
def test_the_probe_backs_off_while_the_upstream_is_down(route: str):
    w = World()
    if route == "cold":
        w.reap_idle()
    else:
        w.degrade_and_give_up()
    w.bus.give_up_on_degrade = True

    attempts: list[float] = []
    for _ in range(120):  # an hour of passes, 30 s apart
        before = len(w.up.connects)
        _cycle(w.probe)
        if len(w.up.connects) > before:
            attempts.append(w.clock.now)
        w.clock.now += 30

    # 30 s doubling to a 600 s cap: t = 0, 30, 90, 210, 450, 930, 1530, 2130, 2730, 3330.
    assert len(attempts) == 10
    gaps = [b - a for a, b in zip(attempts, attempts[1:], strict=False)]
    assert gaps == sorted(gaps) and max(gaps) == 600
    assert w.group.select_member() is None

    # Back up: the next due attempt starts both, and the backoff is forgotten.
    w.up.healthy = True
    w.clock.now += 600
    _cycle(w.probe)
    assert w.states() == {"a": "ready", "b": "ready"}
    assert w.group.select_member() is not None
    assert w.probe._backoff == {}


def test_rebalance_leaves_a_stuck_cold_group_with_a_selectable_member():
    w = World()
    w.reap_idle()
    w.up.healthy = True

    w.group.rebalance()  # what hangar_group_rebalance calls

    assert w.group.circuit_open is False
    member = w.group.select_member()
    assert member is not None
    # The call that selected it starts it.
    member.ensure_ready(by_call=True)
    assert member.state is McpServerState.READY


def test_rebalance_still_takes_given_up_and_degraded_members_out():
    w = World(stuck=False)
    w.degrade_and_give_up()
    w.group.rebalance()
    assert w.group.select_member() is None
    assert not any(w.group.get_member(m).in_rotation for m in MEMBERS)


def test_control_members_that_stay_ready_heal_through_health_checks_without_the_probe():
    w = World()
    w.up.healthy = True
    for _ in range(3):
        w.all_workers_once()
    assert w.group.select_member() is not None
    assert w.group.circuit_open is False
    assert w.up.connects == []  # healed by checks, not started


def test_bootstrap_builds_the_probe_over_the_live_groups_without_starting_it(monkeypatch):
    from mcp_hangar.server.bootstrap import workers as workers_module

    monkeypatch.setattr(workers_module, "get_runtime", lambda: type("R", (), {"repository": {}})())
    built = workers_module.create_background_workers()
    probe = [w for w in built if w.task == "group_recovery"]
    assert len(probe) == 1
    assert probe[0]._groups is workers_module.GROUPS
    assert probe[0].running is False


def test_a_member_stopped_on_purpose_is_not_started_again():
    w = World()
    w.reap_idle()
    w.up.healthy = True
    w.servers["a"]._state = McpServerState.READY
    w.servers["a"]._client = FakeClient(w.up)
    w.servers["a"].shutdown(reason="manual")  # an operator's stop of a member that had failed out
    w.bus.publish_aggregate_events("mcp_server", "a", list(w.servers["a"].collect_events()))

    _cycle(w.probe)

    assert w.up.connects == ["b"]
    assert w.servers["a"].state is McpServerState.COLD


def test_a_member_of_two_stuck_groups_is_started_once_per_pass():
    w = World()
    w.reap_idle()
    other = McpServerGroup(group_id="other", auto_start=False, unhealthy_threshold=1)
    other.add_member(w.servers["a"])
    other.report_failure("a")  # out of rotation on a failure, as in "pool"
    w.groups["other"] = other

    _cycle(w.probe)  # upstream still down

    assert sorted(w.up.connects) == ["a", "b"]


def test_rebalance_puts_back_a_member_whose_start_failed():
    w = World()
    w.reap_idle()
    _cycle(w.probe)  # upstream down: both starts fail
    assert {s.dead_reason_snapshot for s in w.servers.values()} == {"start_failed"}

    w.group.rebalance()

    assert w.group.select_member() is not None


def test_a_pass_stopped_midway_starts_nothing_more():
    w = World()
    w.reap_idle()
    w.up.healthy = True
    w.servers["a"]._create_client = lambda: (w.probe.stop(), FakeClient(w.up))[1]  # type: ignore[method-assign]
    w.probe.running = True

    w.probe.probe_once()

    assert w.servers["b"].state is McpServerState.COLD
