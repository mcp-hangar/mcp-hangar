"""Tests for EventBus with Event Store integration."""

import pytest

from mcp_hangar.domain.contracts.event_store import ConcurrencyError
from mcp_hangar.domain.events import DomainEvent, ProviderStarted, ProviderStateChanged
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence import InMemoryEventStore


class TestEventBusWithEventStore:
    """Tests for EventBus with event persistence."""

    @pytest.fixture
    def event_store(self) -> InMemoryEventStore:
        return InMemoryEventStore()

    @pytest.fixture
    def event_bus(self, event_store: InMemoryEventStore) -> EventBus:
        return EventBus(event_store=event_store)

    def test_publish_to_stream_persists_events(self, event_bus: EventBus, event_store: InMemoryEventStore):
        event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )

        new_version = event_bus.publish_to_stream(
            stream_id="provider:math",
            events=[event],
            expected_version=-1,
        )

        assert new_version == 0
        persisted = event_store.read_stream("provider:math")
        assert len(persisted) == 1
        assert persisted[0].provider_id == "math"

    def test_publish_to_stream_notifies_handlers(self, event_bus: EventBus):
        received_events: list[DomainEvent] = []

        def handler(event: DomainEvent):
            received_events.append(event)

        event_bus.subscribe(ProviderStarted, handler)

        event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )

        event_bus.publish_to_stream(
            stream_id="provider:math",
            events=[event],
            expected_version=-1,
        )

        assert len(received_events) == 1
        assert received_events[0].provider_id == "math"

    def test_publish_to_stream_with_concurrency_error(self, event_bus: EventBus, event_store: InMemoryEventStore):
        event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )

        # First append
        event_bus.publish_to_stream(
            stream_id="provider:math",
            events=[event],
            expected_version=-1,
        )

        # Second append with wrong version
        with pytest.raises(ConcurrencyError):
            event_bus.publish_to_stream(
                stream_id="provider:math",
                events=[event],
                expected_version=-1,  # Wrong - should be 0
            )

    def test_publish_aggregate_events(self, event_bus: EventBus, event_store: InMemoryEventStore):
        events = [
            ProviderStarted(
                provider_id="math",
                mode="subprocess",
                tools_count=3,
                startup_duration_ms=50.0,
            ),
            ProviderStateChanged(
                provider_id="math",
                old_state="COLD",
                new_state="READY",
            ),
        ]

        new_version = event_bus.publish_aggregate_events(
            aggregate_type="provider",
            aggregate_id="math",
            events=events,
            expected_version=-1,
        )

        assert new_version == 1
        persisted = event_store.read_stream("provider:math")
        assert len(persisted) == 2

    def test_publish_empty_events_list(self, event_bus: EventBus):
        version = event_bus.publish_to_stream(
            stream_id="provider:math",
            events=[],
            expected_version=-1,
        )

        assert version == -1

    def test_event_store_property(self, event_bus: EventBus, event_store: InMemoryEventStore):
        assert event_bus.event_store is event_store

    def test_set_event_store(self, event_bus: EventBus):
        new_store = InMemoryEventStore()

        event_bus.set_event_store(new_store)

        assert event_bus.event_store is new_store


class TestEventBusWithoutEventStore:
    """Tests for EventBus without event persistence (NullEventStore)."""

    def test_publish_without_store_uses_null_store(self):
        event_bus = EventBus()  # No store provided
        received_events: list[DomainEvent] = []

        def handler(event: DomainEvent):
            received_events.append(event)

        event_bus.subscribe(ProviderStarted, handler)

        event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )

        # publish() doesn't persist
        event_bus.publish(event)

        assert len(received_events) == 1

    def test_publish_to_stream_with_null_store(self):
        event_bus = EventBus()  # Uses NullEventStore

        event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )

        # Should work but not persist
        version = event_bus.publish_to_stream(
            stream_id="provider:math",
            events=[event],
            expected_version=-1,
        )

        assert version == 0  # NullEventStore increments version
