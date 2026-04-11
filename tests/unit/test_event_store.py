"""Tests for Event Store infrastructure."""

import tempfile

import pytest

from mcp_hangar.domain.events import ProviderStarted, ProviderStateChanged, ProviderStopped
from mcp_hangar.infrastructure.event_store import (
    ConcurrencyError,
    EventStoreSnapshot,
    FileEventStore,
    InMemoryEventStore,
    StoredEvent,
)


class TestStoredEvent:
    """Test StoredEvent dataclass."""

    def test_stored_event_creation(self):
        """Test creating a stored event."""
        event = StoredEvent(
            stream_id="provider-1",
            version=1,
            event_type="ProviderStarted",
            event_id="evt-123",
            occurred_at=1234567890.0,
            data={"provider_id": "provider-1", "mode": "subprocess"},
        )

        assert event.stream_id == "provider-1"
        assert event.version == 1
        assert event.event_type == "ProviderStarted"
        assert event.event_id == "evt-123"

    def test_stored_event_to_dict(self):
        """Test stored event to dictionary conversion."""
        event = StoredEvent(
            stream_id="p1",
            version=1,
            event_type="Event",
            event_id="e1",
            occurred_at=1000.0,
            data={},
            stored_at=2000.0,
        )

        d = event.to_dict()

        assert d["stream_id"] == "p1"
        assert d["version"] == 1
        assert d["stored_at"] == 2000.0

    def test_stored_event_from_dict(self):
        """Test stored event from dictionary."""
        d = {
            "stream_id": "p1",
            "version": 2,
            "event_type": "Event",
            "event_id": "e2",
            "occurred_at": 1000.0,
            "data": {"key": "value"},
            "stored_at": 2000.0,
        }

        event = StoredEvent.from_dict(d)

        assert event.stream_id == "p1"
        assert event.version == 2
        assert event.data == {"key": "value"}


class TestInMemoryEventStore:
    """Test InMemoryEventStore implementation."""

    def test_append_to_new_stream(self):
        """Test appending events to a new stream."""
        store = InMemoryEventStore()

        event = ProviderStarted(
            provider_id="p1",
            mode="subprocess",
            tools_count=5,
            startup_duration_ms=100.0,
        )

        version = store.append("p1", [event], expected_version=-1)

        assert version == 0
        assert store.stream_exists("p1")

    def test_append_multiple_events(self):
        """Test appending multiple events."""
        store = InMemoryEventStore()

        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            ProviderStateChanged("p1", "cold", "ready"),
        ]

        version = store.append("p1", events, expected_version=-1)

        assert version == 1  # 0 and 1

    def test_append_to_existing_stream(self):
        """Test appending to existing stream."""
        store = InMemoryEventStore()

        # First event
        event1 = ProviderStarted("p1", "subprocess", 5, 100.0)
        store.append("p1", [event1], expected_version=-1)

        # Second event
        event2 = ProviderStopped("p1", "idle")
        version = store.append("p1", [event2], expected_version=0)

        assert version == 1

    def test_append_concurrency_error(self):
        """Test that wrong expected version raises ConcurrencyError."""
        store = InMemoryEventStore()

        event = ProviderStarted("p1", "subprocess", 5, 100.0)
        store.append("p1", [event], expected_version=-1)

        # Try to append with wrong expected version
        event2 = ProviderStopped("p1", "idle")

        with pytest.raises(ConcurrencyError) as exc:
            store.append("p1", [event2], expected_version=5)

        assert exc.value.expected_version == 5
        assert exc.value.actual_version == 0

    def test_load_events(self):
        """Test loading events from stream."""
        store = InMemoryEventStore()

        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            ProviderStateChanged("p1", "cold", "ready"),
            ProviderStopped("p1", "idle"),
        ]
        store.append("p1", events, expected_version=-1)

        loaded = store.load("p1")

        assert len(loaded) == 3
        assert loaded[0].event_type == "ProviderStarted"
        assert loaded[1].event_type == "ProviderStateChanged"
        assert loaded[2].event_type == "ProviderStopped"

    def test_load_from_version(self):
        """Test loading events from specific version."""
        store = InMemoryEventStore()

        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            ProviderStateChanged("p1", "cold", "ready"),
            ProviderStopped("p1", "idle"),
        ]
        store.append("p1", events, expected_version=-1)

        loaded = store.load("p1", from_version=1)

        assert len(loaded) == 2
        assert loaded[0].version == 1

    def test_load_to_version(self):
        """Test loading events up to specific version."""
        store = InMemoryEventStore()

        events = [
            ProviderStarted("p1", "subprocess", 5, 100.0),
            ProviderStateChanged("p1", "cold", "ready"),
            ProviderStopped("p1", "idle"),
        ]
        store.append("p1", events, expected_version=-1)

        loaded = store.load("p1", from_version=0, to_version=1)

        assert len(loaded) == 2
        assert loaded[-1].version == 1

    def test_load_nonexistent_stream(self):
        """Test loading from non-existent stream returns empty list."""
        store = InMemoryEventStore()

        loaded = store.load("nonexistent")

        assert loaded == []

    def test_get_version(self):
        """Test getting current version of stream."""
        store = InMemoryEventStore()

        assert store.get_version("p1") == -1

        event = ProviderStarted("p1", "subprocess", 5, 100.0)
        store.append("p1", [event], expected_version=-1)

        assert store.get_version("p1") == 0

    def test_get_all_stream_ids(self):
        """Test getting all stream IDs."""
        store = InMemoryEventStore()

        store.append("p1", [ProviderStarted("p1", "subprocess", 1, 100.0)], -1)
        store.append("p2", [ProviderStarted("p2", "docker", 2, 200.0)], -1)

        ids = store.get_all_stream_ids()

        assert set(ids) == {"p1", "p2"}

    def test_stream_exists(self):
        """Test checking if stream exists."""
        store = InMemoryEventStore()

        assert not store.stream_exists("p1")

        store.append("p1", [ProviderStarted("p1", "subprocess", 1, 100.0)], -1)

        assert store.stream_exists("p1")

    def test_subscribe_to_events(self):
        """Test subscribing to new events."""
        store = InMemoryEventStore()
        received = []

        store.subscribe(lambda e: received.append(e))

        event = ProviderStarted("p1", "subprocess", 5, 100.0)
        store.append("p1", [event], expected_version=-1)

        assert len(received) == 1
        assert received[0].event_type == "ProviderStarted"

    def test_clear_store(self):
        """Test clearing the store."""
        store = InMemoryEventStore()

        store.append("p1", [ProviderStarted("p1", "subprocess", 1, 100.0)], -1)
        store.append("p2", [ProviderStarted("p2", "docker", 2, 200.0)], -1)

        store.clear()

        assert store.total_events == 0
        assert not store.stream_exists("p1")

    def test_total_events(self):
        """Test getting total event count."""
        store = InMemoryEventStore()

        assert store.total_events == 0

        store.append(
            "p1",
            [
                ProviderStarted("p1", "subprocess", 1, 100.0),
                ProviderStopped("p1", "idle"),
            ],
            -1,
        )

        assert store.total_events == 2


class TestFileEventStore:
    """Test FileEventStore implementation."""

    def test_append_and_load(self):
        """Test appending and loading events from file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileEventStore(tmpdir)

            event = ProviderStarted("p1", "subprocess", 5, 100.0)
            store.append("p1", [event], expected_version=-1)

            loaded = store.load("p1")

            assert len(loaded) == 1
            assert loaded[0].event_type == "ProviderStarted"

    def test_persistence_across_instances(self):
        """Test that events persist across store instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # First instance - write
            store1 = FileEventStore(tmpdir)
            event = ProviderStarted("p1", "subprocess", 5, 100.0)
            store1.append("p1", [event], expected_version=-1)

            # Second instance - read
            store2 = FileEventStore(tmpdir)
            loaded = store2.load("p1")

            assert len(loaded) == 1
            assert loaded[0].event_type == "ProviderStarted"

    def test_concurrency_check(self):
        """Test optimistic concurrency in file store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileEventStore(tmpdir)

            event = ProviderStarted("p1", "subprocess", 5, 100.0)
            store.append("p1", [event], expected_version=-1)

            with pytest.raises(ConcurrencyError):
                store.append("p1", [event], expected_version=5)


class TestEventStoreSnapshot:
    """Test EventStoreSnapshot."""

    def test_save_and_load_snapshot(self):
        """Test saving and loading snapshots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshots = EventStoreSnapshot(tmpdir)

            state = {"state": "ready", "tools": ["add", "sub"]}
            snapshots.save_snapshot("p1", version=10, state=state)

            loaded = snapshots.load_snapshot("p1")

            assert loaded["stream_id"] == "p1"
            assert loaded["version"] == 10
            assert loaded["state"] == state

    def test_load_nonexistent_snapshot(self):
        """Test loading non-existent snapshot returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshots = EventStoreSnapshot(tmpdir)

            loaded = snapshots.load_snapshot("nonexistent")

            assert loaded is None

    def test_should_snapshot(self):
        """Test snapshot interval logic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshots = EventStoreSnapshot(tmpdir, snapshot_interval=50)

            assert not snapshots.should_snapshot(49)
            assert snapshots.should_snapshot(50)
            assert snapshots.should_snapshot(100)

    def test_clear_snapshots(self):
        """Test clearing snapshots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshots = EventStoreSnapshot(tmpdir)

            snapshots.save_snapshot("p1", 10, {"state": "ready"})
            snapshots.save_snapshot("p2", 20, {"state": "cold"})

            snapshots.clear()

            assert snapshots.load_snapshot("p1") is None
            assert snapshots.load_snapshot("p2") is None
