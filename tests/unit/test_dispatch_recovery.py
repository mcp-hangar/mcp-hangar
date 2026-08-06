"""Events that were stored but never delivered are delivered on the next start.

`publish_to_stream` commits the append and *then* calls handlers. A process that
died between the two left the events durably in the store, no handler having
seen them, and nothing that would ever look again -- at-most-once, on the path
the project describes publicly as an audit trail.

The crash is simulated the only honest way: by appending straight to the store,
which is exactly the state a process leaves behind when it dies after the
commit and before the delivery loop.
"""

from __future__ import annotations

import pytest

from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.contracts.event_store import NullEventStore
from mcp_hangar.domain.events import ToolInvocationCompleted
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence import (
    InMemoryDispatchCheckpoint,
    InMemoryEventStore,
    SqliteDispatchCheckpoint,
)
from mcp_hangar.stream_ids import MCP_SERVER, stream_id_for

STREAM = stream_id_for(MCP_SERVER, "math")


def _event(tool: str) -> ToolInvocationCompleted:
    return ToolInvocationCompleted(mcp_server_id="math", tool_name=tool, duration_ms=1.0)


@pytest.fixture
def wired() -> tuple[EventBus, InMemoryEventStore, InMemoryDispatchCheckpoint, list]:
    store = InMemoryEventStore()
    checkpoint = InMemoryDispatchCheckpoint()
    bus = EventBus(event_store=store, dispatch_checkpoint=checkpoint)
    seen: list = []
    bus.subscribe_to_all(seen.append, kind=HandlerKind.EFFECT)
    return bus, store, checkpoint, seen


class TestRecoveryAfterACrash:
    def test_events_stored_but_never_delivered_are_delivered_on_sweep(self, wired) -> None:
        bus, store, checkpoint, seen = wired

        # The crash: the append committed, the delivery loop never ran.
        store.append(STREAM, [_event("add"), _event("mul")], expected_version=-1)
        assert seen == [], "precondition: nothing was delivered before the sweep"

        delivered = bus.dispatch_pending()

        assert delivered == 2
        assert [type(e).__name__ for e in seen] == ["ToolInvocationCompleted"] * 2
        assert [e.tool_name for e in seen] == ["add", "mul"], "delivered in append order"

    def test_a_second_sweep_delivers_nothing(self, wired) -> None:
        bus, store, _checkpoint, seen = wired
        store.append(STREAM, [_event("add")], expected_version=-1)

        assert bus.dispatch_pending() == 1
        assert bus.dispatch_pending() == 0
        assert len(seen) == 1, "the checkpoint must survive within the process too"

    def test_a_normal_publish_leaves_nothing_for_the_sweep(self, wired) -> None:
        bus, _store, _checkpoint, seen = wired

        bus.publish_to_stream(STREAM, [_event("add")], expected_version=-1)
        assert len(seen) == 1

        # Without the checkpoint advancing after delivery, this would deliver the
        # same event a second time on every restart, forever.
        assert bus.dispatch_pending() == 0
        assert len(seen) == 1

    def test_only_the_undelivered_tail_is_replayed(self, wired) -> None:
        bus, store, _checkpoint, seen = wired

        bus.publish_to_stream(STREAM, [_event("delivered")], expected_version=-1)
        store.append(STREAM, [_event("lost")], expected_version=0)

        assert bus.dispatch_pending() == 1
        assert [e.tool_name for e in seen] == ["delivered", "lost"]


class TestTheNonDurableStoreIsNotSweptIntoSilence:
    """The trap this capability flag exists for.

    `NullEventStore` accepts appends and keeps nothing. Had delivery been
    rewritten to read from the log, `event_store.enabled: false` would have
    dropped every event silently -- the same class of defect as the phantom
    store this whole track is unwinding.
    """

    def test_publish_still_delivers_without_a_durable_store(self) -> None:
        bus = EventBus(event_store=NullEventStore(), dispatch_checkpoint=InMemoryDispatchCheckpoint())
        seen: list = []
        bus.subscribe_to_all(seen.append, kind=HandlerKind.EFFECT)

        bus.publish_to_stream(STREAM, [_event("add")], expected_version=-1)

        assert len(seen) == 1, "delivery must not depend on the store keeping anything"

    def test_sweeping_a_store_that_keeps_nothing_is_a_no_op(self) -> None:
        bus = EventBus(event_store=NullEventStore(), dispatch_checkpoint=InMemoryDispatchCheckpoint())
        assert bus.dispatch_pending() == 0
        assert NullEventStore().can_replay is False


class TestWithoutACheckpointNothingChanges:
    def test_the_bus_behaves_exactly_as_before(self) -> None:
        store = InMemoryEventStore()
        bus = EventBus(event_store=store)  # no checkpoint
        seen: list = []
        bus.subscribe_to_all(seen.append, kind=HandlerKind.EFFECT)

        bus.publish_to_stream(STREAM, [_event("add")], expected_version=-1)

        assert len(seen) == 1
        assert bus.dispatch_pending() == 0, "no checkpoint means no recovery, not a crash"


class TestTheCheckpointItself:
    def test_it_never_moves_backwards(self) -> None:
        checkpoint = InMemoryDispatchCheckpoint()
        checkpoint.advance(5)
        checkpoint.advance(3)
        assert checkpoint.read() == 5

    def test_sqlite_checkpoint_survives_a_restart(self, tmp_path) -> None:
        db = tmp_path / "events.db"
        SqliteDispatchCheckpoint(db).advance(7)
        # A new object, as a new process would build.
        assert SqliteDispatchCheckpoint(db).read() == 7

    def test_sqlite_checkpoint_never_moves_backwards(self, tmp_path) -> None:
        db = tmp_path / "events.db"
        checkpoint = SqliteDispatchCheckpoint(db)
        checkpoint.advance(9)
        checkpoint.advance(2)
        assert checkpoint.read() == 9

    def test_it_refuses_an_in_memory_database(self) -> None:
        # `:memory:` is per-connection, so this object would track a different
        # database from the store it claims to follow.
        with pytest.raises(ValueError, match="per-connection"):
            SqliteDispatchCheckpoint(":memory:")


class TestTheCheckpointFollowsTheStoreItTracks:
    """A durable mark over a volatile log is worse than no mark at all.

    A configured `sqlite` store that degrades to in-memory still reads as
    "sqlite" in the config, so keying the choice on the driver name would leave
    a file-backed checkpoint asserting delivery of events the volatile log no
    longer holds -- and the next start would skip them.
    """

    def _install(self, store, tmp_path):
        from types import SimpleNamespace

        from mcp_hangar.server.bootstrap.event_store import _install_dispatch_checkpoint

        bus = SimpleNamespace(checkpoint=None)
        bus.set_dispatch_checkpoint = lambda cp: setattr(bus, "checkpoint", cp)
        _install_dispatch_checkpoint(SimpleNamespace(event_bus=bus), store, {"path": str(tmp_path / "events.db")})
        return bus.checkpoint

    def test_a_degraded_memory_store_gets_a_volatile_checkpoint(self, tmp_path) -> None:
        checkpoint = self._install(InMemoryEventStore(), tmp_path)
        assert isinstance(checkpoint, InMemoryDispatchCheckpoint)

    def test_a_sqlite_store_gets_a_durable_one(self, tmp_path) -> None:
        from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore

        checkpoint = self._install(SQLiteEventStore(tmp_path / "events.db"), tmp_path)
        assert isinstance(checkpoint, SqliteDispatchCheckpoint)
