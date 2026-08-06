"""An event that names an aggregate is that aggregate's history, not a notification.

There were two publish methods and only one of them kept a record. `publish()`
delivered and forgot -- it said so in its own docstring -- while
`publish_to_stream()` appended first. Thirty-four call sites used the forgetful
one against ten that did not, so the store held a fraction of what happened: an
`McpServer` stream could begin with `McpServerUpdated`, because registering the
server went through `publish()` and was never written down (#772).

"Which method should I call" is not a question a caller can get right reliably,
and getting it wrong is silent. So the stream is now derived from the event and
`publish()` persists by construction.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import (
    McpServerRegistered,
    McpServerStarted,
    ToolInvocationCompleted,
)
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore


@pytest.fixture
def bus_and_store() -> tuple[EventBus, InMemoryEventStore]:
    store = InMemoryEventStore()
    return EventBus(store), store


def _rows(store: InMemoryEventStore, stream_id: str) -> list[Any]:
    return list(store.read_stream(stream_id))


class TestAnEventThatNamesAnAggregateIsKept:
    def test_registration_reaches_the_stream(self, bus_and_store) -> None:
        # The case from #772: `created: true` over the REST API, and zero rows.
        bus, store = bus_and_store

        bus.publish(McpServerRegistered(mcp_server_id="probe", source="api", mode="subprocess"))

        assert [type(e).__name__ for e in _rows(store, "mcp_server:probe")] == ["McpServerRegistered"]

    def test_the_aggregate_history_starts_at_its_beginning(self, bus_and_store) -> None:
        # Before, the first row was whatever happened *after* registration, so a
        # stream started at version 0 with an edit to something that, as far as
        # the log knew, had never been created.
        bus, store = bus_and_store

        bus.publish(McpServerRegistered(mcp_server_id="probe", source="api", mode="subprocess"))
        bus.publish(McpServerStarted(mcp_server_id="probe", mode="subprocess", tools_count=0, startup_duration_ms=1.0))

        assert [type(e).__name__ for e in _rows(store, "mcp_server:probe")] == [
            "McpServerRegistered",
            "McpServerStarted",
        ]

    def test_each_aggregate_gets_its_own_stream(self, bus_and_store) -> None:
        bus, store = bus_and_store

        bus.publish(McpServerStarted(mcp_server_id="one", mode="subprocess", tools_count=0, startup_duration_ms=1.0))
        bus.publish(McpServerStarted(mcp_server_id="two", mode="subprocess", tools_count=0, startup_duration_ms=1.0))

        assert len(_rows(store, "mcp_server:one")) == 1
        assert len(_rows(store, "mcp_server:two")) == 1


class TestDeliveryIsUnchanged:
    def test_a_handler_still_sees_the_event_exactly_once(self, bus_and_store) -> None:
        # Persisting is added *around* delivery, not instead of it -- and not
        # twice: `publish` now routes through `publish_to_stream`, which delivers
        # too, so an event could easily have been handed over twice.
        bus, _store = bus_and_store
        seen: list = []
        bus.subscribe_to_all(seen.append, kind=HandlerKind.EFFECT)

        bus.publish(McpServerStarted(mcp_server_id="probe", mode="subprocess", tools_count=0, startup_duration_ms=1.0))

        assert len(seen) == 1

    def test_delivery_survives_a_store_that_cannot_write(self) -> None:
        # The contract that predates this change: a broken store must not take
        # enforcement, metrics and audit handlers down with it. Worth pinning
        # here because the failure path used to re-enter `publish`, which now
        # routes back into the store -- an unbounded recursion, entered exactly
        # when the store is already failing.
        class BrokenStore(InMemoryEventStore):
            def append(self, *args: Any, **kwargs: Any) -> int:
                raise OSError("disk is gone")

        bus = EventBus(BrokenStore())
        seen: list = []
        bus.subscribe_to_all(seen.append, kind=HandlerKind.EFFECT)

        bus.publish(McpServerStarted(mcp_server_id="probe", mode="subprocess", tools_count=0, startup_duration_ms=1.0))

        assert len(seen) == 1


class TestEventsWithNoAggregateAreDeliveredOnly:
    def test_they_are_not_written_anywhere(self, bus_and_store) -> None:
        # An authentication is not any aggregate's history.
        # Inventing a bucket for them would make the log harder to read rather
        # than more complete.
        from mcp_hangar.domain.events import AuthenticationSucceeded

        bus, store = bus_and_store
        seen: list = []
        bus.subscribe_to_all(seen.append, kind=HandlerKind.EFFECT)

        bus.publish(
            AuthenticationSucceeded(principal_id="ops", principal_type="user", auth_method="oidc", source_ip="10.0.0.1")
        )

        assert len(seen) == 1
        assert store.list_streams() == []


class TestRecoveryDoesNotRewriteHistory:
    def test_the_startup_sweep_delivers_without_appending(self) -> None:
        # These events came *out* of the store. Publishing them would append
        # them again, so every restart would add a duplicate copy of its own
        # tail -- a log that grows by rereading itself.
        from mcp_hangar.infrastructure.persistence.dispatch_checkpoint import InMemoryDispatchCheckpoint

        store = InMemoryEventStore()
        bus = EventBus(store, dispatch_checkpoint=InMemoryDispatchCheckpoint())
        bus.publish(McpServerRegistered(mcp_server_id="probe", source="api", mode="subprocess"))
        before = len(_rows(store, "mcp_server:probe"))

        seen: list = []
        bus.subscribe_to_all(seen.append, kind=HandlerKind.EFFECT)
        bus.dispatch_pending()

        assert len(_rows(store, "mcp_server:probe")) == before, "recovery must not re-append what it read"


class TestToolInvocationsStillLandWhereTheyDid:
    def test_an_invocation_is_recorded_against_its_server(self, bus_and_store) -> None:
        # This one already persisted, through a different door. It has to keep
        # landing in the same stream, or the history endpoint changes shape.
        bus, store = bus_and_store

        bus.publish(ToolInvocationCompleted(mcp_server_id="probe", tool_name="add", duration_ms=1.0))

        assert len(_rows(store, "mcp_server:probe")) == 1
