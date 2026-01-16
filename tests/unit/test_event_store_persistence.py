"""Tests for new Event Store implementations (IEventStore interface).

Covers SQLiteEventStore and InMemoryEventStore from persistence module.
"""

from pathlib import Path
import threading

import pytest

from mcp_hangar.domain.contracts.event_store import ConcurrencyError, NullEventStore
from mcp_hangar.domain.events import ProviderStarted, ProviderStateChanged, ProviderStopped, ToolInvocationCompleted
from mcp_hangar.infrastructure.persistence import InMemoryEventStore, SQLiteEventStore


class TestNullEventStore:
    """Tests for NullEventStore (null object pattern)."""

    def test_append_returns_incremented_version(self):
        store = NullEventStore()
        event = ProviderStarted(
            provider_id="test",
            mode="subprocess",
            tools_count=5,
            startup_duration_ms=100.0,
        )

        version = store.append("stream:test", [event], expected_version=-1)

        assert version == 0  # -1 + 1 event

    def test_read_stream_returns_empty(self):
        store = NullEventStore()

        events = store.read_stream("stream:test")

        assert events == []

    def test_read_all_returns_empty(self):
        store = NullEventStore()

        events = list(store.read_all())

        assert events == []

    def test_get_stream_version_returns_minus_one(self):
        store = NullEventStore()

        version = store.get_stream_version("stream:test")

        assert version == -1


class TestInMemoryEventStorePersistence:
    """Tests for InMemoryEventStore from persistence module."""

    @pytest.fixture
    def store(self) -> InMemoryEventStore:
        return InMemoryEventStore()

    def test_append_to_new_stream(self, store: InMemoryEventStore):
        event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )

        version = store.append("provider:math", [event], expected_version=-1)

        assert version == 0
        assert store.get_stream_version("provider:math") == 0

    def test_append_multiple_events(self, store: InMemoryEventStore):
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
                new_state="INITIALIZING",
            ),
        ]

        version = store.append("provider:math", events, expected_version=-1)

        assert version == 1  # 0-indexed: event 0 and event 1
        assert store.get_event_count() == 2

    def test_append_with_wrong_version_raises_concurrency_error(self, store: InMemoryEventStore):
        event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )
        store.append("provider:math", [event], expected_version=-1)

        with pytest.raises(ConcurrencyError) as exc_info:
            store.append("provider:math", [event], expected_version=-1)

        assert exc_info.value.stream_id == "provider:math"
        assert exc_info.value.expected == -1
        assert exc_info.value.actual == 0

    def test_read_stream_returns_events_in_order(self, store: InMemoryEventStore):
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
                new_state="INITIALIZING",
            ),
            ProviderStateChanged(
                provider_id="math",
                old_state="INITIALIZING",
                new_state="READY",
            ),
        ]
        store.append("provider:math", events, expected_version=-1)

        read_events = store.read_stream("provider:math")

        assert len(read_events) == 3
        assert isinstance(read_events[0], ProviderStarted)
        assert isinstance(read_events[1], ProviderStateChanged)
        assert read_events[1].new_state == "INITIALIZING"
        assert read_events[2].new_state == "READY"

    def test_read_stream_from_version(self, store: InMemoryEventStore):
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
                new_state="INITIALIZING",
            ),
            ProviderStateChanged(
                provider_id="math",
                old_state="INITIALIZING",
                new_state="READY",
            ),
        ]
        store.append("provider:math", events, expected_version=-1)

        read_events = store.read_stream("provider:math", from_version=1)

        assert len(read_events) == 2
        assert read_events[0].new_state == "INITIALIZING"

    def test_read_nonexistent_stream_returns_empty(self, store: InMemoryEventStore):
        events = store.read_stream("provider:nonexistent")

        assert events == []

    def test_read_all_returns_global_order(self, store: InMemoryEventStore):
        event1 = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )
        event2 = ProviderStarted(
            provider_id="sqlite",
            mode="docker",
            tools_count=5,
            startup_duration_ms=100.0,
        )

        store.append("provider:math", [event1], expected_version=-1)
        store.append("provider:sqlite", [event2], expected_version=-1)

        all_events = list(store.read_all())

        assert len(all_events) == 2
        pos1, stream1, ev1 = all_events[0]
        pos2, stream2, ev2 = all_events[1]

        assert pos1 == 1
        assert stream1 == "provider:math"
        assert pos2 == 2
        assert stream2 == "provider:sqlite"

    def test_read_all_with_limit(self, store: InMemoryEventStore):
        for i in range(10):
            event = ProviderStarted(
                provider_id=f"provider-{i}",
                mode="subprocess",
                tools_count=i,
                startup_duration_ms=float(i),
            )
            store.append(f"provider:{i}", [event], expected_version=-1)

        all_events = list(store.read_all(limit=5))

        assert len(all_events) == 5

    def test_clear_removes_all_events(self, store: InMemoryEventStore):
        event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )
        store.append("provider:math", [event], expected_version=-1)

        store.clear()

        assert store.get_event_count() == 0
        assert store.get_stream_count() == 0
        assert store.get_stream_version("provider:math") == -1


class TestSQLiteEventStorePersistence:
    """Tests for SQLiteEventStore from persistence module."""

    @pytest.fixture
    def store(self) -> SQLiteEventStore:
        """Create in-memory SQLite store for testing."""
        return SQLiteEventStore(":memory:")

    def test_append_to_new_stream(self, store: SQLiteEventStore):
        event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )

        version = store.append("provider:math", [event], expected_version=-1)

        assert version == 0
        assert store.get_stream_version("provider:math") == 0

    def test_append_multiple_events(self, store: SQLiteEventStore):
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
                new_state="INITIALIZING",
            ),
        ]

        version = store.append("provider:math", events, expected_version=-1)

        assert version == 1
        assert store.get_event_count() == 2

    def test_optimistic_concurrency_check(self, store: SQLiteEventStore):
        event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )
        store.append("provider:math", [event], expected_version=-1)

        # Try appending with wrong version
        with pytest.raises(ConcurrencyError) as exc_info:
            store.append("provider:math", [event], expected_version=-1)

        assert exc_info.value.stream_id == "provider:math"
        assert exc_info.value.expected == -1
        assert exc_info.value.actual == 0

    def test_sequential_appends_with_correct_versions(self, store: SQLiteEventStore):
        event1 = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )
        event2 = ProviderStateChanged(
            provider_id="math",
            old_state="COLD",
            new_state="READY",
        )
        event3 = ProviderStopped(provider_id="math", reason="shutdown")

        v1 = store.append("provider:math", [event1], expected_version=-1)
        v2 = store.append("provider:math", [event2], expected_version=v1)
        v3 = store.append("provider:math", [event3], expected_version=v2)

        assert v1 == 0
        assert v2 == 1
        assert v3 == 2
        assert store.get_stream_version("provider:math") == 2

    def test_read_stream_deserializes_events(self, store: SQLiteEventStore):
        events = [
            ProviderStarted(
                provider_id="math",
                mode="subprocess",
                tools_count=3,
                startup_duration_ms=50.0,
            ),
            ToolInvocationCompleted(
                provider_id="math",
                tool_name="add",
                correlation_id="corr-123",
                duration_ms=10.5,
                result_size_bytes=256,
            ),
        ]
        store.append("provider:math", events, expected_version=-1)

        read_events = store.read_stream("provider:math")

        assert len(read_events) == 2
        assert isinstance(read_events[0], ProviderStarted)
        assert read_events[0].provider_id == "math"
        assert read_events[0].tools_count == 3

        assert isinstance(read_events[1], ToolInvocationCompleted)
        assert read_events[1].tool_name == "add"
        assert read_events[1].duration_ms == 10.5

    def test_read_all_across_streams(self, store: SQLiteEventStore):
        event1 = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )
        event2 = ProviderStarted(
            provider_id="sqlite",
            mode="docker",
            tools_count=5,
            startup_duration_ms=100.0,
        )

        store.append("provider:math", [event1], expected_version=-1)
        store.append("provider:sqlite", [event2], expected_version=-1)

        all_events = list(store.read_all())

        assert len(all_events) == 2
        # Global positions should be sequential
        assert all_events[0][0] < all_events[1][0]

    def test_persistence_across_connections(self, tmp_path: Path):
        db_path = tmp_path / "events.db"
        event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )

        # Write with first connection
        store1 = SQLiteEventStore(db_path)
        store1.append("provider:math", [event], expected_version=-1)

        # Read with new connection
        store2 = SQLiteEventStore(db_path)
        events = store2.read_stream("provider:math")

        assert len(events) == 1
        assert events[0].provider_id == "math"

    def test_get_all_stream_ids(self, store: SQLiteEventStore):
        event1 = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=3,
            startup_duration_ms=50.0,
        )
        event2 = ProviderStarted(
            provider_id="sqlite",
            mode="docker",
            tools_count=5,
            startup_duration_ms=100.0,
        )

        store.append("provider:math", [event1], expected_version=-1)
        store.append("provider:sqlite", [event2], expected_version=-1)

        stream_ids = store.get_all_stream_ids()

        assert set(stream_ids) == {"provider:math", "provider:sqlite"}

    def test_empty_events_list_returns_expected_version(self, store: SQLiteEventStore):
        version = store.append("provider:math", [], expected_version=-1)

        assert version == -1
        assert store.get_stream_version("provider:math") == -1


class TestEventStoreThreadSafety:
    """Concurrent access tests for event stores."""

    def test_concurrent_appends_to_different_streams(self):
        store = InMemoryEventStore()
        errors: list[Exception] = []

        def append_events(stream_id: str):
            try:
                for i in range(100):
                    event = ProviderStarted(
                        provider_id=stream_id,
                        mode="subprocess",
                        tools_count=i,
                        startup_duration_ms=float(i),
                    )
                    current_version = store.get_stream_version(stream_id)
                    store.append(stream_id, [event], expected_version=current_version)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=append_events, args=(f"provider:{i}",)) for i in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert store.get_event_count() == 500  # 5 streams * 100 events

    def test_concurrent_appends_to_same_stream_with_retries(self):
        store = InMemoryEventStore()
        success_count = 0
        lock = threading.Lock()

        def append_with_retry():
            nonlocal success_count
            for _ in range(10):
                for attempt in range(100):  # Max retries
                    try:
                        event = ProviderStarted(
                            provider_id="shared",
                            mode="subprocess",
                            tools_count=1,
                            startup_duration_ms=1.0,
                        )
                        version = store.get_stream_version("provider:shared")
                        store.append("provider:shared", [event], expected_version=version)
                        with lock:
                            success_count += 1
                        break
                    except ConcurrencyError:
                        continue

        threads = [threading.Thread(target=append_with_retry) for _ in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 50 events (5 threads * 10 events) should be persisted
        assert success_count == 50
        assert store.get_event_count() == 50
