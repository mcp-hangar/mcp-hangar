"""A session suspended on one replica is refused by all of them.

The registry is a set in one process. A session suspended by a detection rule on
replica A was refused by A and served by B and C, so a caller who retried the
request got through -- an enforcement decision anyone could walk past without
knowing it was there.

This is the one piece of state in phase 3 that crosses the replica boundary, and
the reason is the subject: a suspension is a decision about the *session*, not
about the pod that happened to take the request that triggered it. Lifecycle
state and circuit breakers stay local because they answer "can I serve this",
which is a question about the pod.
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.event_handlers.session_suspension_projection import SessionSuspensionProjection
from mcp_hangar.application.services.event_tailer import EventTailer
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import SessionSuspended, SessionUnsuspended
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
from mcp_hangar.infrastructure.session_suspension import InMemorySessionSuspensionRegistry


class _Replica:
    """One gateway: its own registry and bus, over a shared log."""

    def __init__(self, instance_id: str, log: InMemoryEventStore) -> None:
        self.instance_id = instance_id
        self.registry = InMemorySessionSuspensionRegistry()
        self.bus = EventBus()
        self.bus.set_event_store(log)
        projection = SessionSuspensionProjection(self.registry)
        self.bus.subscribe(SessionSuspended, projection.handle, kind=HandlerKind.PROJECTION)
        self.bus.subscribe(SessionUnsuspended, projection.handle, kind=HandlerKind.PROJECTION)
        self.tailer = EventTailer(log, self.bus, instance_id)

    def suspends(self, session_id: str) -> None:
        self.bus.publish(SessionSuspended(session_id=session_id, reason="detection", source="rule-1"))

    def lifts(self, session_id: str) -> None:
        self.bus.publish(SessionUnsuspended(session_id=session_id, source="api"))

    def refuses(self, session_id: str) -> bool:
        return session_id in self.registry


@pytest.fixture
def replicas() -> tuple[_Replica, _Replica]:
    log = InMemoryEventStore()
    return _Replica("gateway-a", log), _Replica("gateway-b", log)


class TestRetryingAgainstAnotherReplicaDoesNotWork:
    def test_the_peer_refuses_the_session_too(self, replicas) -> None:
        # The bypass, closed. Before this, B served the session A had just
        # suspended, and the caller only had to try again.
        a, b = replicas

        a.suspends("s-1")
        b.tailer.tick()

        assert a.refuses("s-1") is True
        assert b.refuses("s-1") is True

    def test_the_suspension_holds_on_the_deciding_replica_immediately(self, replicas) -> None:
        # Applied locally by the write path, not only by the projection. If the
        # projection were the only thing that applied it, a deployment that
        # failed to subscribe it would return 200 and block nothing anywhere.
        a, _b = replicas

        a.suspends("s-1")

        assert a.refuses("s-1") is True

    def test_lifting_it_reaches_the_peer_as_well(self, replicas) -> None:
        # More urgent than it looks: a lift that reaches one replica leaves the
        # session refused by the others, which the caller experiences as an
        # intermittent block nobody can explain.
        a, b = replicas
        a.suspends("s-1")
        b.tailer.tick()

        a.lifts("s-1")
        b.tailer.tick()

        assert a.refuses("s-1") is False
        assert b.refuses("s-1") is False

    def test_a_suspension_reaches_a_replica_that_was_not_running_yet(self, replicas) -> None:
        # A pod added by a rollout must not come up serving a session the fleet
        # has already blocked... which it does not: its cursor starts at the
        # head. This is the known limit, and it is written down rather than
        # implied -- the fleet-wide record is the log, and a joining replica
        # reads a *snapshot* of the fleet, not of session state.
        a, _b = replicas
        a.suspends("s-old")

        late = _Replica("gateway-c", a.bus.event_store)
        late.tailer.tick()

        assert late.refuses("s-old") is False


class TestItIsAProjectionAndNotAnEffect:
    def test_a_peers_suspension_is_applied_rather_than_skipped(self, replicas) -> None:
        # Classified as an effect, it would run only on the replica that
        # produced it -- which is exactly the bug.
        import inspect

        from mcp_hangar.server.bootstrap import event_handlers

        source = inspect.getsource(event_handlers.init_event_handlers)
        subscriptions = [line for line in source.splitlines() if "suspension_projection.handle" in line]

        # Both of them, checked one by one. Asserting that the *block* mentions
        # PROJECTION passes while one of the two says EFFECT -- which is exactly
        # what a probe found, and what a half-classified pair looks like: the
        # suspension crosses replicas and the lift does not, so a session stays
        # blocked on peers forever.
        assert len(subscriptions) == 2
        for line in subscriptions:
            assert "HandlerKind.PROJECTION" in line, line
            assert "HandlerKind.EFFECT" not in line, line

    def test_applying_it_twice_changes_nothing(self, replicas) -> None:
        # The tail is at-least-once, so a projection has to be idempotent.
        a, b = replicas
        a.suspends("s-1")

        b.tailer.tick()
        b.tailer.tick()

        assert b.refuses("s-1") is True

    def test_the_deciding_replica_does_not_double_apply(self, replicas) -> None:
        # Its own event is skipped by its own tail, so the local write and the
        # tail cannot fight over the same session.
        a, _b = replicas
        a.suspends("s-1")

        a.tailer.tick()

        assert a.refuses("s-1") is True


class TestTheDecisionIsRecorded:
    def test_a_suspension_goes_into_the_log(self, replicas) -> None:
        # It has to be in the log or the tail has nothing to carry. Session
        # events name no server, so they get their own stream rather than
        # falling through to "delivered and not stored".
        a, _b = replicas

        a.suspends("s-1")

        stored = [event for _position, _stream, event in a.bus.event_store.read_all()]
        assert [type(event).__name__ for event in stored] == ["SessionSuspended"]

    def test_it_lands_in_a_session_stream(self) -> None:
        from mcp_hangar.stream_ids import stream_id_for_event

        assert stream_id_for_event(SessionSuspended(session_id="s-1")) == "session:s-1"

    def test_an_event_naming_a_server_and_a_session_still_belongs_to_the_server(self) -> None:
        # `DetectionRuleMatched` carries both. The session is context there, not
        # the subject, and routing it away from the server's history would take
        # the detection out of the story the server's stream tells.
        from mcp_hangar.domain.events import DetectionRuleMatched
        from mcp_hangar.stream_ids import stream_id_for_event

        event = DetectionRuleMatched(
            rule_id="r", rule_name="r", severity="high", session_id="s-1", mcp_server_id="math"
        )

        assert stream_id_for_event(event) == "mcp_server:math"
