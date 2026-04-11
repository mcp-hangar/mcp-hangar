"""Event Store contract - interface for domain event persistence.

The Event Store provides append-only persistence for domain events,
enabling Event Sourcing pattern with optimistic concurrency control.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from ..events import DomainEvent
from ..exceptions import CompactionError  # noqa: F401 -- re-exported for consumers of this module


class ConcurrencyError(Exception):
    """Raised when optimistic concurrency check fails.

    This occurs when attempting to append events to a stream with
    an expected version that doesn't match the actual stream version.
    """

    def __init__(self, stream_id: str, expected: int, actual: int):
        """Initialize concurrency error.

        Args:
            stream_id: The stream that had the conflict.
            expected: Expected version at time of append.
            actual: Actual version found in store.
        """
        self.stream_id = stream_id
        self.expected = expected
        self.actual = actual
        super().__init__(f"Concurrency conflict on stream '{stream_id}': expected version {expected}, actual {actual}")


class StreamNotFoundError(Exception):
    """Raised when attempting to read a non-existent stream."""

    def __init__(self, stream_id: str):
        self.stream_id = stream_id
        super().__init__(f"Stream not found: {stream_id}")


class IEventStore(ABC):
    """Interface for domain event persistence.

    Event Store is an append-only log of domain events organized into streams.
    Each stream represents an aggregate's event history.

    Stream IDs follow convention: "{aggregate_type}:{aggregate_id}"
    Example: "provider:math", "provider_group:default"

    Version numbers:
    - -1 means "no stream exists" (for new aggregates)
    - 0+ is the actual version (count of events - 1)
    """

    @abstractmethod
    def append(
        self,
        stream_id: str,
        events: list[DomainEvent],
        expected_version: int,
    ) -> int:
        """Append events to a stream with optimistic concurrency control.

        Events are appended atomically. Either all events are persisted
        or none are (in case of concurrency conflict).

        Args:
            stream_id: Identifier of the event stream.
            events: List of domain events to append.
            expected_version: Expected current version of stream.
                Use -1 for new streams (no events yet).

        Returns:
            New version of the stream after append.

        Raises:
            ConcurrencyError: When expected_version doesn't match actual.
        """

    @abstractmethod
    def read_stream(
        self,
        stream_id: str,
        from_version: int = 0,
    ) -> list[DomainEvent]:
        """Read all events from a stream.

        Args:
            stream_id: Identifier of the event stream.
            from_version: Start reading from this version (inclusive).
                Defaults to 0 (read all events).

        Returns:
            List of domain events in order of occurrence.
            Empty list if stream doesn't exist.
        """

    @abstractmethod
    def read_all(
        self,
        from_position: int = 0,
        limit: int = 1000,
    ) -> Iterator[tuple[int, str, DomainEvent]]:
        """Read all events across all streams (for projections).

        Used to build read models by processing all events in order.

        Args:
            from_position: Global position to start from (exclusive).
                Use 0 to read from beginning.
            limit: Maximum number of events to return.

        Yields:
            Tuples of (global_position, stream_id, event).
        """

    @abstractmethod
    def get_stream_version(self, stream_id: str) -> int:
        """Get current version of a stream.

        Args:
            stream_id: Identifier of the event stream.

        Returns:
            Current version number, or -1 if stream doesn't exist.
        """

    @abstractmethod
    def list_streams(self, prefix: str = "") -> list[str]:
        """List all stream IDs, optionally filtered by prefix.

        Args:
            prefix: Optional prefix to filter streams.

        Returns:
            List of stream IDs matching the prefix.
        """

    @abstractmethod
    def save_snapshot(
        self,
        stream_id: str,
        version: int,
        state: dict[str, Any],
    ) -> None:
        """Save an aggregate snapshot at a given version.

        Snapshots accelerate aggregate loading by storing state at a point
        in time, so only subsequent events need replaying.

        Args:
            stream_id: Stream identifier (matches event stream).
            version: Stream version this snapshot represents.
            state: Serialized aggregate state (must be JSON-serializable).
        """

    @abstractmethod
    def load_snapshot(
        self,
        stream_id: str,
    ) -> dict[str, Any] | None:
        """Load the latest snapshot for a stream.

        Args:
            stream_id: Stream identifier.

        Returns:
            Dict with "version" and "state" keys, or None if no snapshot exists.
        """

    @abstractmethod
    def compact_stream(self, stream_id: str) -> int:
        """Delete events that precede the latest snapshot for a stream.

        Compaction reduces storage by removing events that are already
        captured in a snapshot. Only events with stream_version less than
        or equal to the snapshot version are deleted.

        Args:
            stream_id: Identifier of the event stream to compact.

        Returns:
            Number of events deleted.

        Raises:
            CompactionError: When no snapshot exists for the stream.
                Compaction without a snapshot would destroy all events
                with no way to reconstruct aggregate state.
        """


class IDurableEventStore(IEventStore):
    """Extended event store interface for durable persistence backends.

    Adds migration, maintenance, and connection management methods
    that are specific to SQLite/Postgres backends. In-memory stores
    do not implement this -- they implement IEventStore directly.

    Enterprise persistence (SQLite/Postgres event stores) implements this.
    Core retains InMemoryEventStore (IEventStore) and NullEventStore.
    """

    @abstractmethod
    def migrate(self) -> None:
        """Run database migrations to ensure schema is up to date.

        Called during bootstrap. Implementations should be idempotent.
        """

    @abstractmethod
    def close(self) -> None:
        """Close database connections and release resources.

        Called during graceful shutdown.
        """

    @abstractmethod
    def get_storage_stats(self) -> dict[str, Any]:
        """Return storage statistics for monitoring.

        Returns:
            Dict with keys like 'total_events', 'total_streams',
            'storage_bytes', 'oldest_event_timestamp'.
        """


class NullEventStore(IEventStore):
    """Null object implementation - discards all events.

    Use when event persistence is disabled or for testing.
    """

    def append(
        self,
        stream_id: str,
        events: list[DomainEvent],
        expected_version: int,
    ) -> int:
        """Accept events but don't persist them."""
        return expected_version + len(events)

    def read_stream(
        self,
        stream_id: str,
        from_version: int = 0,
    ) -> list[DomainEvent]:
        """Return empty list (no events persisted)."""
        return []

    def read_all(
        self,
        from_position: int = 0,
        limit: int = 1000,
    ) -> Iterator[tuple[int, str, DomainEvent]]:
        """Yield nothing (no events persisted)."""
        return iter([])

    def get_stream_version(self, stream_id: str) -> int:
        """Return -1 (stream doesn't exist)."""
        return -1

    def list_streams(self, prefix: str = "") -> list[str]:
        """Return empty list (no streams)."""
        return []

    def save_snapshot(
        self,
        stream_id: str,
        version: int,
        state: dict[str, Any],
    ) -> None:
        """Accept but discard snapshots."""

    def load_snapshot(
        self,
        stream_id: str,
    ) -> dict[str, Any] | None:
        """Return None (no snapshots persisted)."""
        return None

    def compact_stream(self, stream_id: str) -> int:
        """No-op: NullEventStore has no events to compact."""
        return 0
