"""Event Store contract - interface for domain event persistence.

The Event Store provides append-only persistence for domain events,
enabling Event Sourcing pattern with optimistic concurrency control.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..events import DomainEvent


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
