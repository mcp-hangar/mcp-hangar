"""Holding the lease, losing it, and stopping when it is lost.

The lease is a row; this is about the thing that keeps it, and about what
happens when it cannot. Two shapes of loss, and only one is an answer:

- `renew` returns None -- the database says the tenure is over. Easy.
- `renew` raises -- the database is unreachable and this instance learns
  nothing. The lease is expiring on the database's clock regardless, and once
  it lapses a peer will take it. Retrying through that is how two instances end
  up managing at once.

So the second case is on a deadline, measured locally, deliberately shorter than
the TTL: give up slightly early rather than slightly late.
"""

from __future__ import annotations

import time

import pytest

from mcp_hangar.application.services.lease_keeper import ManagementLeaseKeeper
from mcp_hangar.domain.contracts.management_lease import IManagementLease, Lease


class _Store(IManagementLease):
    """A lease under the test's control, with no clock of its own."""

    def __init__(self) -> None:
        self.granted: Lease | None = None
        self.generation = 0
        self.free = True
        self.renewable = True
        self.raises: Exception | None = None
        self.released: list[Lease] = []
        self.acquires = 0

    def acquire(self, holder: str, ttl_s: float) -> Lease | None:
        self.acquires += 1
        if self.raises is not None:
            raise self.raises
        if not self.free:
            return None
        self.generation += 1
        self.granted = Lease(holder=holder, generation=self.generation, expires_at=time.time() + ttl_s)
        return self.granted

    def renew(self, lease: Lease, ttl_s: float) -> Lease | None:
        if self.raises is not None:
            raise self.raises
        if not self.renewable:
            return None
        return Lease(holder=lease.holder, generation=lease.generation, expires_at=time.time() + ttl_s)

    def release(self, lease: Lease) -> None:
        self.released.append(lease)

    def current(self) -> Lease | None:
        return self.granted


def _keeper(store: _Store, **kwargs) -> ManagementLeaseKeeper:
    defaults = {"ttl_s": 15.0, "interval_s": 0.02, "renew_deadline_s": 0.1}
    defaults.update(kwargs)
    return ManagementLeaseKeeper(store, "gateway-a", **defaults)


@pytest.fixture(autouse=True)
def restore_the_process_keeper():
    """Put the module-level keeper back, whatever a test did to it.

    Cleaning up with `monkeypatch.setattr(coordination, "_keeper", None)` inside
    a test looks like a reset and is the opposite: monkeypatch records the value
    at the moment it is called -- the keeper -- and restores *that* at teardown.
    A keeper holding nothing then leaked into every later test in the session,
    where `may_manage()` answered False and things that should have run did not.
    Which is exactly the failure it caused: the circuit-breaker save, a file
    away and passing on its own.
    """
    from mcp_hangar.server.bootstrap import coordination

    before = coordination._keeper
    yield
    coordination._keeper = before


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestTakingTheLease:
    def test_a_started_keeper_acquires_and_reports_it_may_manage(self) -> None:
        store = _Store()
        keeper = _keeper(store)
        try:
            keeper.start()

            assert _wait_until(keeper.may_manage)
        finally:
            keeper.stop()

    def test_it_does_not_manage_before_it_is_started(self) -> None:
        # Bootstrap assembles, lifecycle starts. A process that is built and
        # never run must not claim to be the manager.
        keeper = _keeper(_Store())

        assert keeper.may_manage() is False

    def test_a_taken_lease_means_this_instance_does_not_manage(self) -> None:
        store = _Store()
        store.free = False
        keeper = _keeper(store)
        try:
            keeper.start()
            time.sleep(0.2)

            assert keeper.may_manage() is False
        finally:
            keeper.stop()

    def test_it_keeps_trying_so_a_follower_can_take_over_without_a_restart(self) -> None:
        store = _Store()
        store.free = False
        keeper = _keeper(store)
        try:
            keeper.start()
            assert _wait_until(lambda: store.acquires >= 2)

            store.free = True

            assert _wait_until(keeper.may_manage)
        finally:
            keeper.stop()

    def test_an_unreachable_store_is_not_a_crash_and_not_a_lease(self) -> None:
        # An instance that never held the lease and cannot reach the store
        # simply is not managing. There is nothing to give up.
        store = _Store()
        store.raises = RuntimeError("connection refused")
        keeper = _keeper(store)
        try:
            keeper.start()
            time.sleep(0.2)

            assert keeper.may_manage() is False
        finally:
            keeper.stop()


class TestLosingIt:
    def test_a_definite_answer_stops_management_at_once(self) -> None:
        store = _Store()
        keeper = _keeper(store)
        try:
            keeper.start()
            assert _wait_until(keeper.may_manage)

            store.renewable = False

            assert _wait_until(lambda: not keeper.may_manage())
        finally:
            keeper.stop()

    def test_a_lost_lease_is_not_released(self) -> None:
        # It belongs to whoever took it. Releasing would take the lease away
        # from the instance that is now the manager.
        store = _Store()
        keeper = _keeper(store)
        try:
            keeper.start()
            assert _wait_until(keeper.may_manage)
            store.renewable = False
            assert _wait_until(lambda: not keeper.may_manage())

            assert store.released == []
        finally:
            keeper.stop()

    def test_an_unreachable_store_costs_the_lease_once_the_deadline_passes(self) -> None:
        # The important one. No answer is available, the tenure is expiring on
        # a clock this instance cannot read, and a peer is about to take over.
        store = _Store()
        keeper = _keeper(store, renew_deadline_s=0.15)
        try:
            keeper.start()
            assert _wait_until(keeper.may_manage)

            store.raises = RuntimeError("the database went away")

            assert _wait_until(lambda: not keeper.may_manage(), timeout=3.0)
        finally:
            keeper.stop()

    def test_a_brief_hiccup_does_not_cost_the_lease(self) -> None:
        # The other side of that: giving up on the first failed renewal would
        # hand management around on every transient network blip, and a fleet
        # whose manager keeps changing converges worse than one whose manager
        # is occasionally slow.
        store = _Store()
        keeper = _keeper(store, renew_deadline_s=5.0)
        try:
            keeper.start()
            assert _wait_until(keeper.may_manage)

            store.raises = RuntimeError("one bad round trip")
            time.sleep(0.1)
            store.raises = None
            time.sleep(0.1)

            assert keeper.may_manage() is True
        finally:
            keeper.stop()

    def test_losing_it_is_announced(self) -> None:
        lost: list[bool] = []
        store = _Store()
        keeper = ManagementLeaseKeeper(
            store, "gateway-a", ttl_s=15.0, interval_s=0.02, renew_deadline_s=0.1, on_lost=lambda: lost.append(True)
        )
        try:
            keeper.start()
            assert _wait_until(keeper.may_manage)
            store.renewable = False

            assert _wait_until(lambda: bool(lost))
        finally:
            keeper.stop()

    def test_taking_it_is_announced_with_the_generation_to_fence_by(self) -> None:
        seen: list[Lease] = []
        store = _Store()
        keeper = ManagementLeaseKeeper(
            store, "gateway-a", ttl_s=15.0, interval_s=0.02, renew_deadline_s=0.1, on_acquired=seen.append
        )
        try:
            keeper.start()
            assert _wait_until(lambda: bool(seen))

            assert seen[0].generation >= 1
        finally:
            keeper.stop()


class TestGivingItUpDeliberately:
    def test_stopping_releases_it_so_a_peer_takes_over_in_seconds(self) -> None:
        # The difference between a rolling update that pauses management for a
        # moment and one that pauses it for a TTL per pod.
        store = _Store()
        keeper = _keeper(store)
        keeper.start()
        assert _wait_until(keeper.may_manage)

        keeper.stop()

        assert len(store.released) == 1

    def test_stopping_stops_management_immediately(self) -> None:
        store = _Store()
        keeper = _keeper(store)
        keeper.start()
        assert _wait_until(keeper.may_manage)

        keeper.stop()

        assert keeper.may_manage() is False

    def test_a_failed_release_does_not_stop_the_shutdown(self) -> None:
        # It costs a peer the wait for the TTL. Refusing to shut down over it
        # would cost more.
        store = _Store()
        keeper = _keeper(store)
        keeper.start()
        assert _wait_until(keeper.may_manage)
        store.raises = RuntimeError("gone")

        class _Failing(_Store):
            def release(self, lease: Lease) -> None:
                raise RuntimeError("cannot reach the database")

        keeper._store = _Failing()
        keeper.stop()

        assert keeper.may_manage() is False


class TestTheDeadlineMustBeUnderTheTtl:
    def test_a_deadline_at_the_ttl_is_refused(self) -> None:
        # An instance that gives up no earlier than the lease expires can still
        # be managing at the moment a peer is entitled to take over, which is
        # the one thing this whole arrangement exists to prevent.
        with pytest.raises(ValueError, match="renew_deadline_s"):
            ManagementLeaseKeeper(_Store(), "gateway-a", ttl_s=15.0, renew_deadline_s=15.0)

    def test_the_defaults_leave_a_margin(self) -> None:
        from mcp_hangar.application.services import lease_keeper

        assert lease_keeper.DEFAULT_RENEW_DEADLINE_S < lease_keeper.DEFAULT_TTL_S
        assert lease_keeper.DEFAULT_INTERVAL_S < lease_keeper.DEFAULT_RENEW_DEADLINE_S


class TestWithoutABackendEverythingManagesAsBefore:
    def test_no_keeper_means_this_instance_manages(self, monkeypatch) -> None:
        # Every deployment that has not opted into `persistence.backend`. It has
        # no peers to disagree with, and must behave exactly as it did.
        from mcp_hangar.server.bootstrap import coordination

        monkeypatch.setattr(coordination, "_keeper", None)

        assert coordination.may_manage() is True

    def test_no_backend_builds_no_keeper(self, monkeypatch) -> None:
        from mcp_hangar.server.bootstrap import composition, coordination

        monkeypatch.setattr(composition, "_persistence_backend", None)

        assert coordination.init_lease_keeper({}) is None

    def test_a_backend_builds_one(self, monkeypatch, tmp_path) -> None:
        from mcp_hangar.infrastructure.persistence.registry import create_backend
        from mcp_hangar.server.bootstrap import composition, coordination

        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        monkeypatch.setattr(composition, "_persistence_backend", backend)
        try:
            keeper = coordination.init_lease_keeper({})
            assert keeper is not None
            assert keeper.may_manage() is False  # not started
        finally:
            backend.close()

    def test_the_ttl_is_configurable(self, monkeypatch, tmp_path) -> None:
        # The default is defended in the ADR; an operator whose storage is
        # slower than ours needs to be able to move it.
        from mcp_hangar.infrastructure.persistence.registry import create_backend
        from mcp_hangar.server.bootstrap import composition, coordination

        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        monkeypatch.setattr(composition, "_persistence_backend", backend)
        try:
            keeper = coordination.init_lease_keeper(
                {"coordination": {"lease_ttl_s": 30, "renew_interval_s": 7, "renew_deadline_s": 20}}
            )
            assert keeper is not None
            assert (keeper._ttl_s, keeper._interval_s, keeper._renew_deadline_s) == (30.0, 7.0, 20.0)
        finally:
            backend.close()
