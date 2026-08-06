"""A follower does not discover, does not expire, does not snapshot.

The gate is asked **per cycle**, not once at startup. A lease lost mid-life has
to stop the next cycle, not the next process -- an instance that checked once
and kept going is precisely the stalled leader the generation exists to fence,
and fencing is a last line rather than a plan.

What is *not* gated matters as much. Garbage collection and health checks act on
this replica's own subprocesses and its own connections to upstreams. Gating
them would leak an idle child process on every follower, and leave followers
unable to notice that an upstream they are serving traffic to has died.
"""

from __future__ import annotations

import asyncio

import pytest

from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryOrchestrator
from mcp_hangar.application.discovery.lifecycle_manager import DiscoveryLifecycleManager


class _Switch:
    """A lease that can be flipped between cycles."""

    def __init__(self, held: bool = True) -> None:
        self.held = held
        self.asked = 0

    def __call__(self) -> bool:
        self.asked += 1
        return self.held


@pytest.mark.asyncio
class TestDiscoveryOnlyRunsOnTheHolder:
    async def test_a_follower_runs_no_cycle(self) -> None:
        # Discovery registers and deregisters servers in storage every replica
        # shares. Three of these is three sources of truth arguing.
        orchestrator = DiscoveryOrchestrator(may_manage=_Switch(held=False))
        cycles: list[int] = []
        orchestrator.run_discovery_cycle = lambda: cycles.append(1)  # type: ignore[assignment]
        orchestrator._running = True

        task = asyncio.create_task(orchestrator._discovery_loop())
        await asyncio.sleep(0.05)
        orchestrator._running = False
        task.cancel()

        assert cycles == []

    async def test_the_holder_runs_them(self) -> None:
        orchestrator = DiscoveryOrchestrator(may_manage=_Switch(held=True))
        cycles: list[int] = []

        async def cycle():
            cycles.append(1)

        orchestrator.run_discovery_cycle = cycle  # type: ignore[assignment]
        orchestrator._running = True

        task = asyncio.create_task(orchestrator._discovery_loop())
        await asyncio.sleep(0.05)
        orchestrator._running = False
        task.cancel()

        assert cycles

    async def test_the_gate_is_asked_again_every_cycle(self) -> None:
        # The property that makes a lost lease stop the *next* cycle. Checked
        # once at startup, a deposed leader would keep converging until the
        # process ended.
        switch = _Switch(held=True)
        orchestrator = DiscoveryOrchestrator(may_manage=switch)
        orchestrator.config.refresh_interval_s = 0.01
        cycles: list[int] = []

        async def cycle():
            cycles.append(1)

        orchestrator.run_discovery_cycle = cycle  # type: ignore[assignment]
        orchestrator._running = True

        task = asyncio.create_task(orchestrator._discovery_loop())
        await asyncio.sleep(0.05)
        switch.held = False
        ran_before = len(cycles)
        await asyncio.sleep(0.08)
        orchestrator._running = False
        task.cancel()

        assert len(cycles) == ran_before, "cycles kept running after the lease was lost"

    async def test_regaining_the_lease_resumes_without_a_restart(self) -> None:
        switch = _Switch(held=False)
        orchestrator = DiscoveryOrchestrator(may_manage=switch)
        orchestrator.config.refresh_interval_s = 0.01
        cycles: list[int] = []

        async def cycle():
            cycles.append(1)

        orchestrator.run_discovery_cycle = cycle  # type: ignore[assignment]
        orchestrator._running = True

        task = asyncio.create_task(orchestrator._discovery_loop())
        await asyncio.sleep(0.03)
        switch.held = True
        await asyncio.sleep(0.05)
        orchestrator._running = False
        task.cancel()

        assert cycles

    async def test_without_a_gate_everything_runs_as_before(self) -> None:
        # A standalone gateway, and every deployment that has not selected a
        # storage backend.
        orchestrator = DiscoveryOrchestrator()
        cycles: list[int] = []

        async def cycle():
            cycles.append(1)

        orchestrator.run_discovery_cycle = cycle  # type: ignore[assignment]
        orchestrator._running = True

        task = asyncio.create_task(orchestrator._discovery_loop())
        await asyncio.sleep(0.05)
        orchestrator._running = False
        task.cancel()

        assert cycles


@pytest.mark.asyncio
class TestTtlExpiryOnlyRunsOnTheHolder:
    async def test_a_follower_deregisters_nothing(self) -> None:
        # The most destructive thing discovery does, and the one a follower on
        # a stale view would get most wrong: it would deregister servers the
        # holder had just registered, and the two would take turns forever.
        manager = DiscoveryLifecycleManager(check_interval=0, may_manage=_Switch(held=False))
        checks: list[int] = []

        async def check():
            checks.append(1)
            return []

        manager._check_expirations = check  # type: ignore[assignment]
        manager._running = True

        task = asyncio.create_task(manager._lifecycle_loop())
        await asyncio.sleep(0.05)
        manager._running = False
        task.cancel()

        assert checks == []

    async def test_the_holder_expires_them(self) -> None:
        manager = DiscoveryLifecycleManager(check_interval=0, may_manage=_Switch(held=True))
        checks: list[int] = []

        async def check():
            checks.append(1)
            return []

        manager._check_expirations = check  # type: ignore[assignment]
        manager._running = True

        task = asyncio.create_task(manager._lifecycle_loop())
        await asyncio.sleep(0.05)
        manager._running = False
        task.cancel()

        assert checks

    async def test_the_orchestrator_passes_its_gate_down(self) -> None:
        # Otherwise the expiry loop would keep deregistering on a follower while
        # discovery itself was correctly idle -- the worst of both.
        switch = _Switch(held=False)
        orchestrator = DiscoveryOrchestrator(may_manage=switch)

        assert orchestrator._lifecycle_manager._may_manage() is False


class TestTheMetricSnapshotIsGatedButGcAndHealthAreNot:
    def test_a_follower_takes_no_snapshot(self) -> None:
        # It writes to storage every replica shares. Three workers on one
        # history table interleave three series into one, and a chart of "calls
        # per minute" then depends on which replica was scheduled.
        from mcp_hangar.gc import MetricsSnapshotWorker

        worker = MetricsSnapshotWorker(interval_s=0, may_manage=_Switch(held=False))
        taken: list[int] = []
        worker._take_snapshot = lambda: taken.append(1)  # type: ignore[assignment]

        worker.start()
        import time

        time.sleep(0.05)
        worker.stop()

        assert taken == []

    def test_the_holder_takes_them(self) -> None:
        from mcp_hangar.gc import MetricsSnapshotWorker

        worker = MetricsSnapshotWorker(interval_s=0, may_manage=_Switch(held=True))
        taken: list[int] = []
        worker._take_snapshot = lambda: taken.append(1)  # type: ignore[assignment]

        worker.start()
        import time

        time.sleep(0.05)
        worker.stop()

        assert taken

    def test_garbage_collection_is_not_gated(self) -> None:
        # It shuts down *this* replica's idle child processes. Gating it would
        # leak one per idle server on every follower, forever.
        import inspect

        from mcp_hangar.gc import BackgroundWorker

        assert "may_manage" not in inspect.signature(BackgroundWorker.__init__).parameters

    def test_health_checks_are_not_gated_either(self) -> None:
        # A health check probes this replica's own path to an upstream. A
        # follower that stopped checking would keep routing traffic to a server
        # it could no longer reach, and would not find out until a call failed.
        import inspect

        from mcp_hangar.gc import BackgroundWorker

        source = inspect.getsource(BackgroundWorker._loop)
        assert "may_manage" not in source
