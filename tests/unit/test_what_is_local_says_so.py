"""What each replica keeps to itself, and where that stops being invisible.

Three of phase 3's four questions came out the same way: **state about this
replica's own resources stays local; state about the fleet is shared.** Lifecycle
state answers "can *I* serve this" -- in subprocess mode each replica runs its
own child, so a shared state field would be plainly false. A circuit breaker
protects this replica's path to an upstream, and sharing it would let one pod
with a network problem cut a healthy server off from the other two. Rate limits
are counted per process, so a configured 10 rps admits 30 across three.

None of that is wrong. What was wrong is that none of it was *visible*: every
number the API returned looked fleet-wide, and there was no way to tell which
replica had answered.

The fourth question, session suspension, went the other way and is tested in
`test_a_suspension_is_not_bypassable.py`.
"""

from __future__ import annotations

import pytest


class TestTheSystemEndpointSaysWhichReplicaAnswered:
    def test_it_reports_an_instance_id(self) -> None:
        from mcp_hangar.domain.events import current_instance_id
        from mcp_hangar.server.api.system import _instance_info

        assert _instance_info()["instance_id"] == current_instance_id()

    def test_a_standalone_gateway_reports_that_it_manages_the_fleet(self, monkeypatch) -> None:
        # No coordination, no peers, so it is its own manager -- and saying
        # `false` here would send an operator looking for a leader that does
        # not exist.
        from mcp_hangar.server.bootstrap import coordination
        from mcp_hangar.server.api.system import _instance_info

        monkeypatch.setattr(coordination, "_keeper", None)
        info = _instance_info()

        assert info["coordinates_with_peers"] is False
        assert info["manages_fleet"] is True

    def test_a_follower_says_it_does_not_manage_the_fleet(self, monkeypatch) -> None:
        # The operator-facing half of the lease. Two replicas answering false
        # while none answers true is a fleet with nothing converging it, and
        # that should be readable directly rather than inferred from what has
        # stopped happening.
        from mcp_hangar.server.bootstrap import composition, coordination
        from mcp_hangar.server.api.system import _instance_info

        monkeypatch.setattr(coordination, "_keeper", _NotTheManager())
        # A keeper is not coordination; a *shareable* backend is. Three replicas
        # on SQLite each had a keeper and each reported that it coordinated.
        monkeypatch.setattr(composition, "_persistence_backend", _ASharedBackend())
        info = _instance_info()

        assert info["coordinates_with_peers"] is True
        assert info["manages_fleet"] is False

    def test_the_holder_says_it_does(self, monkeypatch) -> None:
        from mcp_hangar.server.bootstrap import coordination
        from mcp_hangar.server.api.system import _instance_info

        monkeypatch.setattr(coordination, "_keeper", _TheManager())

        assert _instance_info()["manages_fleet"] is True

    def test_it_states_that_rate_limits_are_per_instance(self) -> None:
        # Rather than quietly multiplying the configured number by the replica
        # count. Dividing it drifts exactly when it matters -- a rollout runs
        # N+1 replicas -- and a shared bucket costs a round trip per call.
        from mcp_hangar.server.api.system import _instance_info

        assert _instance_info()["rate_limits_are_per_instance"] is True


class TestTheSharedCircuitBreakerRowHasOneWriter:
    def test_a_follower_does_not_write_it(self, monkeypatch) -> None:
        # Each replica keeps its own breaker, deliberately -- but they all
        # shared one row and all wrote it on the way out, so a rolling update
        # ended with whichever pod stopped last having overwritten the others.
        from mcp_hangar.server.bootstrap import coordination, cqrs

        monkeypatch.setattr(coordination, "_keeper", _NotTheManager())
        store = _RecordingStore()

        cqrs.save_group_circuit_breakers(store, {"g-1": _Group()})

        assert store.checkpoints == []

    def test_the_holder_writes_it(self, monkeypatch) -> None:
        from mcp_hangar.server.bootstrap import coordination, cqrs

        monkeypatch.setattr(coordination, "_keeper", _TheManager())
        store = _RecordingStore()

        cqrs.save_group_circuit_breakers(store, {"g-1": _Group()})

        assert [saga_id for _type, saga_id in store.checkpoints] == ["g-1"]

    def test_a_standalone_gateway_still_writes_it(self, monkeypatch) -> None:
        # It has no peers to disagree with, and losing the breaker state across
        # restarts would be a regression for every existing deployment.
        from mcp_hangar.server.bootstrap import coordination, cqrs

        monkeypatch.setattr(coordination, "_keeper", None)
        store = _RecordingStore()

        cqrs.save_group_circuit_breakers(store, {"g-1": _Group()})

        assert len(store.checkpoints) == 1

    def test_the_lease_is_released_after_the_row_is_written(self) -> None:
        # Ordering, asserted because getting it wrong is silent: release first
        # and the leader stops being the leader a moment before doing the one
        # thing only the leader may do, so nobody writes the row at all.
        import inspect

        from mcp_hangar.server.lifecycle import ServerLifecycle

        source = inspect.getsource(ServerLifecycle.shutdown)

        assert source.index("self._context.shutdown()") < source.index("keeper.stop()")


class _ASharedBackend:
    shared_across_instances = True


class _NotTheManager:
    ttl_s = 15.0

    def may_manage(self) -> bool:
        return False

    @property
    def lease(self):
        return None

    @property
    def incumbent(self):
        # A follower knows who does hold it -- that is the point of the field.
        from mcp_hangar.domain.contracts.management_lease import Lease

        return Lease("the-other-replica", 4, 1_800_000_100.0)


class _TheManager:
    ttl_s = 15.0

    def may_manage(self) -> bool:
        return True

    @property
    def lease(self):
        from mcp_hangar.domain.contracts.management_lease import Lease

        return Lease(holder="gateway-a", generation=1, expires_at=0.0)

    @property
    def incumbent(self):
        return self.lease


class _RecordingStore:
    def __init__(self) -> None:
        self.checkpoints: list[tuple[str, str]] = []

    def checkpoint(self, saga_type: str, saga_id: str, state_data: dict, last_event_position: int) -> None:
        self.checkpoints.append((saga_type, saga_id))


class _Group:
    def __init__(self) -> None:
        from mcp_hangar.domain.model.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

        self._circuit_breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=5))


@pytest.fixture(autouse=True)
def restore_the_process_keeper():
    """The module-level keeper is process-wide; put it back after each test."""
    from mcp_hangar.server.bootstrap import coordination

    before = coordination._keeper
    yield
    coordination._keeper = before
