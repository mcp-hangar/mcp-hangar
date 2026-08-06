"""Two replicas, one PostgreSQL: what A does, B learns; what A did, A keeps once.

The unit tests drive a tailer over an in-memory store, which proves the logic
and not the arrangement. This stands up two independent buses over two
independent stores against **one database** -- which is what three pods are --
and checks the property the whole of phase 2 exists for:

    an event produced on A is delivered once on A and once on B.

Not twice anywhere, and not zero times on B.

Opt-in, like the other `live` tests. See
`tests/integration/test_postgres_tail_does_not_skip.py` for the podman one-liner.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os

import pytest

from mcp_hangar.application.services.event_tailer import EventTailer
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.domain.events.producer import set_instance_id
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.backends.postgresql.event_store import PostgresEventStore

pytestmark = pytest.mark.live

DSN = os.environ.get("HANGAR_TEST_POSTGRES_DSN", "")

psycopg2 = pytest.importorskip("psycopg2", reason="the postgres extra is not installed")

if not DSN:
    pytest.skip("HANGAR_TEST_POSTGRES_DSN is not set", allow_module_level=True)


@dataclass
class _ThingHappened(DomainEvent):
    thing: str = "x"


class _DirectFactory:
    @contextmanager
    def get_connection(self):
        conn = psycopg2.connect(DSN)
        try:
            yield conn
        finally:
            conn.close()


class _Replica:
    """One gateway: its own bus, its own store handle, one shared database.

    The identity is **minted**, as it is in production -- `set_instance_id`
    takes a label and appends a per-process suffix, so a replica's identity is
    never the label it was given. Writing the harness the other way round, with
    the tailer told the label while events carried the minted id, produced a
    tailer that skipped nothing: it is worth knowing that the failure looks
    exactly like "the skip does not work" rather than like a mismatch.
    """

    def __init__(self, label: str, prefix: str) -> None:
        self.instance_id = set_instance_id(label)
        self.view: list[DomainEvent] = []
        self.exported: list[DomainEvent] = []

        self.store = PostgresEventStore(_DirectFactory(), table_prefix=prefix)
        self.store.initialize()
        self.bus = EventBus()
        self.bus.set_event_store(self.store)
        self.bus.subscribe(_ThingHappened, self.view.append, kind=HandlerKind.PROJECTION)
        self.bus.subscribe(_ThingHappened, self.exported.append, kind=HandlerKind.EFFECT)
        self.tailer = EventTailer(self.store, self.bus, self.instance_id)

    def publish(self, stream: str, thing: str) -> None:
        """Publish as this replica would: locally *and* into the shared log.

        The process identity is what an event picks up at construction, so it
        has to be this replica's for the duration. Two replicas in one process
        is the contrivance; everything else here is what production does.
        """
        import mcp_hangar.domain.events.producer as producer

        before = producer._instance_id
        producer._instance_id = self.instance_id
        try:
            self.bus.publish_to_stream(stream, [_ThingHappened(thing=thing)], -1)
        finally:
            producer._instance_id = before


@pytest.fixture
def replicas():
    prefix = "twonode_"
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {prefix}events, {prefix}streams, {prefix}snapshots")
        conn.commit()
    # Constructed together, so both cursors start at the same empty head.
    yield _Replica("gateway-a", prefix), _Replica("gateway-b", prefix)
    with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {prefix}events, {prefix}streams, {prefix}snapshots")
        conn.commit()


class TestOneEventReachesEachReplicaExactlyOnce:
    def test_the_producer_keeps_it_once_and_the_peer_gets_it_once(self, replicas) -> None:
        a, b = replicas

        a.publish("mcp_server:math", "started")
        a.tailer.tick()
        b.tailer.tick()

        assert len(a.view) == 1, "the producer applied its own event twice"
        assert len(b.view) == 1, "the peer did not see it, or saw it more than once"

    def test_polling_again_changes_nothing(self, replicas) -> None:
        a, b = replicas
        a.publish("mcp_server:math", "started")

        for _ in range(3):
            a.tailer.tick()
            b.tailer.tick()

        assert (len(a.view), len(b.view)) == (1, 1)

    def test_the_siem_receives_one_copy_per_event_across_the_fleet(self, replicas) -> None:
        # The property phase 2 is really about. Two replicas, two events, two
        # exports -- one per event, on the replica that produced it.
        a, b = replicas

        a.publish("mcp_server:math", "started")
        b.publish("mcp_server:weather", "started")
        a.tailer.tick()
        b.tailer.tick()

        assert len(a.exported) == 1
        assert len(b.exported) == 1
        assert [event.thing for event in a.exported] == ["started"]

    def test_both_replicas_end_with_the_same_view(self, replicas) -> None:
        # Which is the point of a projection: after the tail has caught up,
        # neither replica's answer depends on where the request landed.
        a, b = replicas

        a.publish("mcp_server:math", "from-a")
        b.publish("mcp_server:weather", "from-b")
        a.tailer.tick()
        b.tailer.tick()

        assert sorted(event.thing for event in a.view) == ["from-a", "from-b"]
        assert sorted(event.thing for event in b.view) == ["from-a", "from-b"]


class TestATailedEventIsNotWrittenBack:
    def test_the_log_does_not_grow_when_a_replica_applies_a_peers_event(self, replicas) -> None:
        # If applying a tailed event published it again, each replica would echo
        # the other's events and the log would grow without bound -- and every
        # replica would keep re-delivering the echoes.
        a, b = replicas
        a.publish("mcp_server:math", "started")
        a.tailer.tick()
        b.tailer.tick()

        with _DirectFactory().get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM twonode_events")
            count = int(cur.fetchone()[0])
            conn.commit()

        assert count == 1


class TestAReplicaThatJoinsLate:
    def test_it_does_not_replay_the_history_of_the_cluster(self, replicas) -> None:
        # Its view comes from the snapshot plus the tail, so the tail must start
        # at the head. Replaying would apply everything that ever happened to
        # every pod that joins -- on every rollout.
        a, _b = replicas
        a.publish("mcp_server:math", "old news")

        late = _Replica("gateway-c", "twonode_")

        assert late.tailer.tick() == 0
        assert late.view == []

    def test_it_sees_everything_after_it_joined(self, replicas) -> None:
        a, _b = replicas
        a.publish("mcp_server:math", "old news")
        late = _Replica("gateway-c", "twonode_")

        a.publish("mcp_server:weather", "fresh news")
        late.tailer.tick()

        assert [event.thing for event in late.view] == ["fresh news"]
