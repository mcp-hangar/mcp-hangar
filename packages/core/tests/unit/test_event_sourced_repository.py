"""Tests for EventSourcedProviderRepository snapshot integration.

Verifies that the repository uses IEventStore snapshot methods for
saving and loading snapshots, and that aggregate replay works correctly
from snapshot + subsequent events.
"""

import pytest

from mcp_hangar.domain.events import (
    ProviderStarted,
    ProviderStateChanged,
    ToolInvocationCompleted,
)
from mcp_hangar.domain.model.event_sourced_provider import EventSourcedProvider
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.event_sourced_repository import EventSourcedProviderRepository
from mcp_hangar.infrastructure.event_store import InMemoryEventStore as OldInMemoryEventStore
from mcp_hangar.infrastructure.persistence import InMemoryEventStore


class TestRepositorySnapshotSaveViaEventStore:
    """Test that repository saves snapshots using event_store.save_snapshot()."""

    @pytest.fixture
    def event_store(self) -> InMemoryEventStore:
        return InMemoryEventStore()

    @pytest.fixture
    def repo(self, event_store: InMemoryEventStore) -> EventSourcedProviderRepository:
        return EventSourcedProviderRepository(
            event_store=event_store,
            event_bus=EventBus(),
            snapshot_interval=3,
        )

    def test_snapshot_saved_after_snapshot_interval_events(
        self, repo: EventSourcedProviderRepository, event_store: InMemoryEventStore
    ):
        """Repository saves snapshot using event_store.save_snapshot() after snapshot_interval events."""
        provider = EventSourcedProvider(
            provider_id="test-provider",
            mode="subprocess",
            command=["python", "-m", "test"],
        )

        # Record enough events to trigger snapshot (interval=3)
        provider._record_event(
            ProviderStateChanged(provider_id="test-provider", old_state="cold", new_state="initializing")
        )
        provider._record_event(
            ProviderStarted(provider_id="test-provider", mode="subprocess", tools_count=2, startup_duration_ms=50.0)
        )
        provider._record_event(
            ToolInvocationCompleted(
                provider_id="test-provider",
                tool_name="add",
                correlation_id="corr-1",
                duration_ms=10.0,
                result_size_bytes=100,
            )
        )

        # Save config first
        repo._save_config("test-provider", provider)

        # Add provider - should trigger snapshot after 3 events
        repo.add("test-provider", provider)

        # Verify snapshot was saved via event_store
        snapshot = event_store.load_snapshot("test-provider")
        assert snapshot is not None
        assert "version" in snapshot
        assert "state" in snapshot

    def test_snapshot_not_saved_before_interval(
        self, repo: EventSourcedProviderRepository, event_store: InMemoryEventStore
    ):
        """Repository does not save snapshot before reaching snapshot_interval."""
        provider = EventSourcedProvider(
            provider_id="test-provider",
            mode="subprocess",
            command=["python", "-m", "test"],
        )

        # Record fewer events than interval (interval=3)
        provider._record_event(
            ProviderStateChanged(provider_id="test-provider", old_state="cold", new_state="initializing")
        )
        provider._record_event(
            ProviderStarted(provider_id="test-provider", mode="subprocess", tools_count=2, startup_duration_ms=50.0)
        )

        repo._save_config("test-provider", provider)
        repo.add("test-provider", provider)

        # Verify no snapshot (only 2 events, interval is 3)
        snapshot = event_store.load_snapshot("test-provider")
        assert snapshot is None


class TestRepositorySnapshotLoadViaEventStore:
    """Test that repository loads from snapshot via event_store.load_snapshot()."""

    @pytest.fixture
    def event_store(self) -> InMemoryEventStore:
        return InMemoryEventStore()

    @pytest.fixture
    def repo(self, event_store: InMemoryEventStore) -> EventSourcedProviderRepository:
        return EventSourcedProviderRepository(
            event_store=event_store,
            event_bus=EventBus(),
            snapshot_interval=3,
        )

    def test_loads_from_snapshot_plus_subsequent_events(
        self, repo: EventSourcedProviderRepository, event_store: InMemoryEventStore
    ):
        """Repository loads from snapshot then replays subsequent events."""
        # First, create a provider and accumulate events to create a snapshot
        provider = EventSourcedProvider(
            provider_id="test-provider",
            mode="subprocess",
            command=["python", "-m", "test"],
        )

        # Record events to trigger snapshot
        provider._record_event(
            ProviderStateChanged(provider_id="test-provider", old_state="cold", new_state="initializing")
        )
        provider._record_event(
            ProviderStarted(provider_id="test-provider", mode="subprocess", tools_count=2, startup_duration_ms=50.0)
        )
        provider._record_event(
            ToolInvocationCompleted(
                provider_id="test-provider",
                tool_name="add",
                correlation_id="corr-1",
                duration_ms=10.0,
                result_size_bytes=100,
            )
        )

        repo._save_config("test-provider", provider)
        repo.add("test-provider", provider)

        # Add more events after snapshot
        provider._record_event(
            ToolInvocationCompleted(
                provider_id="test-provider",
                tool_name="multiply",
                correlation_id="corr-2",
                duration_ms=20.0,
                result_size_bytes=200,
            )
        )
        repo.add("test-provider", provider)

        # Clear cache to force reload
        repo.invalidate_cache("test-provider")

        # Load from snapshot + subsequent events
        loaded = repo.get("test-provider")

        assert loaded is not None

    def test_loads_correctly_when_no_snapshot_exists(
        self, repo: EventSourcedProviderRepository, event_store: InMemoryEventStore
    ):
        """Repository loads correctly via full replay when no snapshot exists."""
        provider = EventSourcedProvider(
            provider_id="test-provider",
            mode="subprocess",
            command=["python", "-m", "test"],
        )

        # Record only 1 event (below snapshot interval)
        provider._record_event(
            ProviderStateChanged(provider_id="test-provider", old_state="cold", new_state="initializing")
        )

        repo._save_config("test-provider", provider)
        repo.add("test-provider", provider)

        # Clear cache
        repo.invalidate_cache("test-provider")

        # Load should work via full replay
        loaded = repo.get("test-provider")
        assert loaded is not None


class TestRepositorySnapshotStateEquivalence:
    """Test that loading from snapshot produces same state as full replay."""

    def test_snapshot_load_matches_full_replay(self):
        """Loading from snapshot + subsequent events produces same state as full replay."""
        event_store = InMemoryEventStore()
        repo = EventSourcedProviderRepository(
            event_store=event_store,
            event_bus=EventBus(),
            snapshot_interval=3,
        )

        # Define events that will be used for both paths
        events_to_record = [
            ProviderStateChanged(provider_id="test-provider", old_state="cold", new_state="initializing"),
            ProviderStarted(provider_id="test-provider", mode="subprocess", tools_count=2, startup_duration_ms=50.0),
            ToolInvocationCompleted(
                provider_id="test-provider",
                tool_name="add",
                correlation_id="corr-1",
                duration_ms=10.0,
                result_size_bytes=100,
            ),
            ToolInvocationCompleted(
                provider_id="test-provider",
                tool_name="multiply",
                correlation_id="corr-2",
                duration_ms=20.0,
                result_size_bytes=200,
            ),
        ]

        # Create provider with applied events (from_events applies them, updating state)
        provider = EventSourcedProvider.from_events(
            provider_id="test-provider",
            mode="subprocess",
            events=events_to_record,
            command=["python", "-m", "test"],
        )

        # Re-record events as uncommitted so repo.add() persists them
        for event in events_to_record:
            provider._record_event(event)

        repo._save_config("test-provider", provider)
        repo.add("test-provider", provider)

        # Get the state from snapshot+replay
        repo.invalidate_cache("test-provider")
        loaded_from_snapshot = repo.get("test-provider")

        # Build state from full replay (no snapshot)
        full_replay = EventSourcedProvider.from_events(
            provider_id="test-provider",
            mode="subprocess",
            events=events_to_record,
            command=["python", "-m", "test"],
        )

        # Both should have the same provider state (lifecycle phase)
        assert loaded_from_snapshot is not None
        assert loaded_from_snapshot._state == full_replay._state
        assert loaded_from_snapshot._health._consecutive_failures == full_replay._health._consecutive_failures
        assert loaded_from_snapshot._health._total_failures == full_replay._health._total_failures


class TestRepositoryBackwardCompatibility:
    """Test backward compat with old EventStoreSnapshot."""

    def test_works_with_old_event_store_no_snapshot_support(self):
        """Repository works when event_store has no snapshot support (old EventStore)."""
        old_store = OldInMemoryEventStore()
        repo = EventSourcedProviderRepository(
            event_store=old_store,
            event_bus=EventBus(),
            snapshot_interval=3,
        )

        provider = EventSourcedProvider(
            provider_id="test-provider",
            mode="subprocess",
            command=["python", "-m", "test"],
        )

        provider._record_event(
            ProviderStateChanged(provider_id="test-provider", old_state="cold", new_state="initializing")
        )
        provider._record_event(
            ProviderStarted(provider_id="test-provider", mode="subprocess", tools_count=2, startup_duration_ms=50.0)
        )
        provider._record_event(
            ToolInvocationCompleted(
                provider_id="test-provider",
                tool_name="add",
                correlation_id="corr-1",
                duration_ms=10.0,
                result_size_bytes=100,
            )
        )

        repo._save_config("test-provider", provider)
        # Should not raise - old store lacks snapshot methods
        repo.add("test-provider", provider)

        # Should load successfully
        repo.invalidate_cache("test-provider")
        loaded = repo.get("test-provider")
        assert loaded is not None
