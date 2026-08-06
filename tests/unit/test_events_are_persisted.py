"""Domain events reach the event store, through the real command path.

`data/events.db` was created on 2026-07-16 and held **zero rows in all four
tables** until this change: every drain point called `EventBus.publish`, which
does not persist, and the only methods that do had no production caller. The
store was configured, health-checked and reported durable the whole time.

These tests run the real handler against a real bus and a real store, because
the defect was precisely that each half worked in isolation.
"""

from __future__ import annotations

import sqlite3

import pytest
from structlog.testing import capture_logs

from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.application.commands.crud_commands import CreateGroupCommand
from mcp_hangar.application.commands.crud_handlers import CreateGroupHandler
from mcp_hangar.domain.contracts.event_store import ConcurrencyError
from mcp_hangar.domain.events import ToolInvocationCompleted
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence import InMemoryEventStore
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore
from mcp_hangar.stream_ids import MCP_SERVER, MCP_SERVER_GROUP, stream_id_for


def _event(tool: str) -> ToolInvocationCompleted:
    return ToolInvocationCompleted(mcp_server_id="math", tool_name=tool, duration_ms=1.0)


class TestTheCommandPathWritesToTheStore:
    def test_creating_a_group_lands_in_that_group_s_stream(self) -> None:
        store = InMemoryEventStore()
        bus = EventBus(event_store=store)
        handler = CreateGroupHandler(groups={}, event_bus=bus)

        handler.handle(CreateGroupCommand(group_id="g", strategy="round_robin"))

        stored = store.read_stream(stream_id_for(MCP_SERVER_GROUP, "g"))
        assert [type(e).__name__ for e in stored] == ["GroupCreated"]

    def test_handlers_still_see_the_event(self) -> None:
        # Persisting must not come at the cost of delivery: metrics, audit,
        # security and enforcement all hang off this path.
        store = InMemoryEventStore()
        bus = EventBus(event_store=store)
        seen: list = []
        bus.subscribe_to_all(seen.append, kind=HandlerKind.EFFECT)

        CreateGroupHandler(groups={}, event_bus=bus).handle(CreateGroupCommand(group_id="g", strategy="round_robin"))

        assert [type(e).__name__ for e in seen] == ["GroupCreated"]


class TestAppendingTwiceToOneAggregate:
    """The bug a naive wiring would have shipped.

    `publish_aggregate_events` used to default to `expected_version=-1`, which
    asserts "this stream does not exist". That holds exactly once per aggregate;
    the second command against the same server would raise `ConcurrencyError`
    from inside a fault barrier and lose its events.
    """

    def test_a_second_batch_does_not_conflict(self) -> None:
        store = InMemoryEventStore()
        bus = EventBus(event_store=store)

        bus.publish_aggregate_events(MCP_SERVER, "math", [_event("add")])
        bus.publish_aggregate_events(MCP_SERVER, "math", [_event("mul")])

        stored = store.read_stream(stream_id_for(MCP_SERVER, "math"))
        assert [e.tool_name for e in stored] == ["add", "mul"]

    def test_an_explicit_version_still_asserts_it(self) -> None:
        # The sentinel is opt-in. A caller that does claim a version keeps the
        # optimistic-concurrency check it asked for.
        store = InMemoryEventStore()
        bus = EventBus(event_store=store)
        bus.publish_aggregate_events(MCP_SERVER, "math", [_event("add")])

        with pytest.raises(ConcurrencyError):
            bus.publish_aggregate_events(MCP_SERVER, "math", [_event("mul")], expected_version=-1)


class TestAStoreOutageDoesNotSwitchOffTheGateway:
    """Persistence must not become a single point of failure for delivery.

    Before this change handlers ran with no store at all. If a failed append
    stopped delivery, a disk-full event would silently switch off enforcement
    and audit while the gateway kept serving traffic -- a regression that adding
    durability must not cause.
    """

    class _BrokenStore(InMemoryEventStore):
        def append(self, stream_id, events, expected_version):  # type: ignore[override]
            raise OSError("disk full")

    def test_events_are_still_delivered(self) -> None:
        bus = EventBus(event_store=self._BrokenStore())
        seen: list = []
        bus.subscribe_to_all(seen.append, kind=HandlerKind.EFFECT)

        bus.publish_aggregate_events(MCP_SERVER, "math", [_event("add")])

        assert len(seen) == 1, "a store outage must not stop metrics, audit and enforcement"

    def test_the_hole_in_the_log_is_reported(self) -> None:
        # `capture_logs` because the project logs through structlog; asserting
        # on `caplog.text` reads the stdlib rendering and passes or fails for
        # reasons that have nothing to do with what was logged.
        bus = EventBus(event_store=self._BrokenStore())
        with capture_logs() as logs:
            bus.publish_aggregate_events(MCP_SERVER, "math", [_event("add")])

        persistence_failures = [entry for entry in logs if entry.get("event") == "event_persistence_failed"]
        assert len(persistence_failures) == 1
        assert persistence_failures[0]["log_level"] == "error"
        assert "audit log has a hole" in persistence_failures[0]["detail"]

    def test_a_real_conflict_is_not_swallowed_as_an_outage(self) -> None:
        # ConcurrencyError means someone else wrote where this caller expected
        # to. That is the caller's business and must not be degraded into a
        # "delivered but unpersisted" warning.
        store = InMemoryEventStore()
        bus = EventBus(event_store=store)
        bus.publish_to_stream(stream_id_for(MCP_SERVER, "math"), [_event("add")], expected_version=-1)

        with pytest.raises(ConcurrencyError):
            bus.publish_to_stream(stream_id_for(MCP_SERVER, "math"), [_event("mul")], expected_version=-1)


class TestOnRealSqlite:
    def test_the_database_is_no_longer_empty(self, tmp_path) -> None:
        """The audit's evidence, inverted.

        The finding that opened this track was `data/events.db` with 0 rows in
        `events`. This asserts against the same table, through the same command
        path, on a real file.
        """
        db = tmp_path / "events.db"
        bus = EventBus(event_store=SQLiteEventStore(db))

        CreateGroupHandler(groups={}, event_bus=bus).handle(CreateGroupCommand(group_id="g", strategy="round_robin"))

        with sqlite3.connect(db) as conn:
            rows = conn.execute("SELECT event_type, stream_id FROM events").fetchall()
        assert rows == [("GroupCreated", "mcp_server_group:g")]
