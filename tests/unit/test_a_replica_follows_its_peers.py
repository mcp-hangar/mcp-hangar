"""A replica applies what its peers did, once, and not what it did itself.

Without the tail, three replicas are three separate views of one fleet: a server
started on A is missing from B's tool catalogue, and which servers a replica
knows about depends on where the load balancer sent each request.

The interesting cases are the two ways of getting it wrong. Delivering a
replica's *own* events back to it doubles every projection it keeps -- and
idempotent handlers hide that until one of them is not. Delivering *effects*
from the tail sends three copies of every audit record.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mcp_hangar.application.services.event_tailer import EventTailer
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore


@dataclass
class _ThingHappened(DomainEvent):
    thing: str = "x"


@pytest.fixture
def shared_log() -> InMemoryEventStore:
    """One store, standing in for a database two replicas share."""
    return InMemoryEventStore()


def _replica(store, instance_id: str) -> tuple[EventBus, list[DomainEvent], list[DomainEvent]]:
    """A bus with one projection and one effect, and the log they share."""
    bus = EventBus()
    bus.set_event_store(store)
    view: list[DomainEvent] = []
    exported: list[DomainEvent] = []
    bus.subscribe(_ThingHappened, view.append, kind=HandlerKind.PROJECTION)
    bus.subscribe(_ThingHappened, exported.append, kind=HandlerKind.EFFECT)
    return bus, view, exported


def _produced_by(instance_id: str, thing: str = "x") -> _ThingHappened:
    event = _ThingHappened(thing=thing)
    object.__setattr__(event, "produced_by", instance_id)
    return event


class TestAPeersEventArrives:
    def test_it_reaches_this_replicas_projection(self, shared_log) -> None:
        bus, view, _exported = _replica(shared_log, "gateway-b")
        tailer = EventTailer(shared_log, bus, "gateway-b")
        shared_log.append("mcp_server:a", [_produced_by("gateway-a")], expected_version=-1)

        tailer.tick()

        assert len(view) == 1

    def test_it_does_not_reach_this_replicas_effects(self, shared_log) -> None:
        # Three replicas, one tool call, one CEF record.
        bus, _view, exported = _replica(shared_log, "gateway-b")
        tailer = EventTailer(shared_log, bus, "gateway-b")
        shared_log.append("mcp_server:a", [_produced_by("gateway-a")], expected_version=-1)

        tailer.tick()

        assert exported == []

    def test_it_arrives_once(self, shared_log) -> None:
        bus, view, _exported = _replica(shared_log, "gateway-b")
        tailer = EventTailer(shared_log, bus, "gateway-b")
        shared_log.append("mcp_server:a", [_produced_by("gateway-a")], expected_version=-1)

        tailer.tick()
        tailer.tick()
        tailer.tick()

        assert len(view) == 1


class TestAReplicaDoesNotFollowItself:
    def test_its_own_event_is_not_applied_a_second_time(self, shared_log) -> None:
        # The whole reason events carry a producer. A replica publishes locally
        # *and* appends to the log it tails, so without this every projection it
        # keeps would count its own work twice.
        bus, view, _exported = _replica(shared_log, "gateway-a")
        tailer = EventTailer(shared_log, bus, "gateway-a")

        bus.publish_to_stream("mcp_server:a", [_produced_by("gateway-a")], -1)
        assert len(view) == 1, "the local publish should have delivered once"

        tailer.tick()

        assert len(view) == 1, "the tail delivered this replica's own event a second time"

    def test_a_peers_event_in_the_same_batch_still_arrives(self, shared_log) -> None:
        # Skipping own events must not skip the batch.
        bus, view, _exported = _replica(shared_log, "gateway-a")
        tailer = EventTailer(shared_log, bus, "gateway-a")
        shared_log.append("mcp_server:a", [_produced_by("gateway-a")], expected_version=-1)
        shared_log.append("mcp_server:b", [_produced_by("gateway-b")], expected_version=-1)

        applied = tailer.tick()

        assert applied == 1
        assert len(view) == 1


class TestTheCursorStartsAtTheHead:
    def test_history_from_before_this_replica_started_is_not_replayed(self, shared_log) -> None:
        # It is already in the snapshot the replica reads at startup. Replaying
        # it would apply the whole history of the cluster to every pod that ever
        # joins.
        shared_log.append("mcp_server:old", [_produced_by("gateway-a")], expected_version=-1)

        bus, view, _exported = _replica(shared_log, "gateway-b")
        tailer = EventTailer(shared_log, bus, "gateway-b")
        tailer.tick()

        assert view == []

    def test_anything_after_the_head_is_delivered(self, shared_log) -> None:
        # The other half of head-before-snapshot: an event that lands after the
        # cursor is taken must not fall in the gap.
        shared_log.append("mcp_server:old", [_produced_by("gateway-a")], expected_version=-1)
        bus, view, _exported = _replica(shared_log, "gateway-b")
        tailer = EventTailer(shared_log, bus, "gateway-b")

        shared_log.append("mcp_server:new", [_produced_by("gateway-a")], expected_version=-1)
        tailer.tick()

        assert len(view) == 1

    def test_the_cursor_is_taken_when_the_tailer_is_built(self) -> None:
        # Stated as a test because bootstrap depends on it: the head has to be
        # capturable *before* the fleet snapshot is read, which means at
        # construction rather than at start().
        import inspect

        assert "tail_head()" in inspect.getsource(EventTailer.__init__)

    def test_bootstrap_takes_the_head_before_it_reads_the_snapshot(self) -> None:
        import inspect
        import sys

        import mcp_hangar.server.bootstrap  # noqa: F401 -- for its side effect on sys.modules

        source = inspect.getsource(sys.modules["mcp_hangar.server.bootstrap"].bootstrap)

        assert source.index("init_event_tailer(runtime)") < source.index("restore_persisted_fleet(runtime)")


class TestItKeepsGoing:
    def test_an_event_that_cannot_be_applied_does_not_stall_the_tail(self, shared_log) -> None:
        # The cursor advances past it. Stopping would wedge this replica's whole
        # view behind one event, and it would serve confidently from the frozen
        # copy.
        bus = EventBus()
        bus.set_event_store(shared_log)
        delivered: list[DomainEvent] = []

        def explode(event: DomainEvent) -> None:
            raise RuntimeError("this projection is broken")

        class _FailingBus(EventBus):
            pass

        bus.deliver_tailed = lambda event: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
        tailer = EventTailer(shared_log, bus, "gateway-b")
        shared_log.append("mcp_server:a", [_produced_by("gateway-a")], expected_version=-1)

        tailer.tick()

        bus.deliver_tailed = delivered.append  # type: ignore[method-assign]
        shared_log.append("mcp_server:b", [_produced_by("gateway-a")], expected_version=-1)
        tailer.tick()

        assert len(delivered) == 1, "the tail stalled behind an event it could not apply"

    def test_a_failing_read_does_not_kill_the_loop(self, shared_log) -> None:
        bus, _view, _exported = _replica(shared_log, "gateway-b")
        tailer = EventTailer(shared_log, bus, "gateway-b", interval_s=0.01)
        calls: list[int] = []

        def read_since(cursor, limit=500):
            calls.append(1)
            raise RuntimeError("the database went away")

        tailer._store = type("_S", (), {"read_since": staticmethod(read_since)})()

        tailer.start()
        try:
            import time

            time.sleep(0.1)
        finally:
            tailer.stop()

        assert len(calls) > 1, "the tailer stopped after the first failed read"


class TestWithoutPeersThereIsNoTail:
    def test_no_storage_backend_means_no_tailer(self, monkeypatch) -> None:
        from mcp_hangar.server.bootstrap import composition, coordination

        monkeypatch.setattr(composition, "_persistence_backend", None)

        assert coordination.init_event_tailer(object()) is None

    def test_a_store_that_keeps_nothing_means_no_tailer(self, monkeypatch) -> None:
        # `NullEventStore` accepts appends and keeps them nowhere, so a tail over
        # it would read silence -- and silence is indistinguishable from "nothing
        # new".
        from types import SimpleNamespace

        from mcp_hangar.domain.contracts.event_store import NullEventStore
        from mcp_hangar.server.bootstrap import composition, coordination

        monkeypatch.setattr(composition, "_persistence_backend", object())
        runtime = SimpleNamespace(event_bus=SimpleNamespace(event_store=NullEventStore()))

        assert coordination.init_event_tailer(runtime) is None
