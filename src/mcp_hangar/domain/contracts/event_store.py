"""Event Store contract - interface for domain event persistence.

The Event Store provides append-only persistence for domain events,
enabling Event Sourcing pattern with optimistic concurrency control.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

from ..events import DomainEvent
from ..exceptions import CompactionError  # noqa: F401 -- re-exported for consumers of this module


@dataclass(frozen=True)
class TailCursor:
    """Where a tailer got to, in whatever terms its store can resume from.

    Deliberately opaque. A position works for a store with one writer, where
    allocation order is commit order; it does not work for PostgreSQL, where a
    row can be allocated position 5, another committed at position 6 first, and
    a cursor already past 6 will never see 5 arrive (measured: it is lost, not
    delayed). That store resumes from a transaction-id watermark instead. A
    caller that treated the cursor as a number would be writing one of those two
    assumptions into code that is supposed to work on both.

    `BEGINNING` means the whole log. `IEventStore.tail_head()` gives the other
    end -- everything committed so far, nothing older to deliver -- which is what
    a replica building a view from a snapshot needs.
    """

    token: str = ""

    def __str__(self) -> str:
        return self.token


#: Read the log from the start.
BEGINNING = TailCursor("")


class TailingNotSupportedError(RuntimeError):
    """Raised when a store cannot be tailed safely and has not said how it could.

    The default `read_since` resumes from a global position, which is only sound
    where writes are serialized. A store that admits concurrent writers and has
    neither overridden the method nor declared itself commit-ordered would skip
    events silently -- so it is refused loudly instead.
    """

    def __init__(self, store: str) -> None:
        super().__init__(
            f"{store} does not declare `positions_are_commit_ordered` and does not override "
            "`read_since`. Resuming from a global position is only safe where appends are "
            "serialized; a store with concurrent writers must implement its own resume token "
            "(see PostgresEventStore) or it will skip events without reporting anything."
        )


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
    Example: "mcp_server:math", "mcp_server_group:default"

    Version numbers:
    - -1 means "no stream exists" (for new aggregates)
    - 0+ is the actual version (count of events - 1)
    """

    @property
    def can_replay(self) -> bool:
        """Whether this store can read back what it was given.

        Delivery recovery reads the log from a checkpoint, which only means
        something for a store that kept the events. `NullEventStore` accepts
        appends and keeps nothing, so a sweep over it would read silence -- and
        silence is indistinguishable from "nothing left to deliver". Callers
        branch on this rather than on `isinstance`.
        """
        return True

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

    #: Whether a higher `global_position` means "committed later".
    #:
    #: True only where appends are serialized -- one writer, or an
    #: in-process lock that every writer goes through. It is False by
    #: default so that a store which never considered the question is refused
    #: rather than allowed to skip events quietly; the two in-tree stores that
    #: qualify say so explicitly.
    positions_are_commit_ordered: ClassVar[bool] = False

    def tail_head(self) -> TailCursor:
        """A cursor meaning "everything committed so far, nothing older".

        Used when a replica builds its view from a snapshot and then follows the
        log: take the head *first*, then the snapshot, and there is no window in
        which an event lands between the two and is missed by both.
        """
        if not self.positions_are_commit_ordered:
            raise TailingNotSupportedError(type(self).__name__)
        last = 0
        for position, _stream_id, _event in self.read_all(from_position=0, limit=1_000_000_000):
            last = position
        return TailCursor(str(last))

    def read_since(
        self,
        cursor: TailCursor,
        limit: int = 1000,
    ) -> tuple[list[tuple[str, DomainEvent]], TailCursor]:
        """Read what was committed after `cursor`, and where to resume.

        The contract a tailer depends on: an event returned once is not returned
        again, and no committed event is passed over. Delivery may lag -- a store
        may hold back an event whose transaction has not resolved -- but it may
        not skip.

        This default resumes from a global position, which is sound only where
        appends are serialized. Anything else must override it.

        Args:
            cursor: Where the last read got to. `BEGINNING` for the whole log.
            limit: Maximum events in one batch.

        Returns:
            The batch as (stream_id, event) pairs, and the cursor to pass next.
        """
        if not self.positions_are_commit_ordered:
            raise TailingNotSupportedError(type(self).__name__)
        position = int(cursor.token) if cursor.token else 0
        batch: list[tuple[str, DomainEvent]] = []
        for next_position, stream_id, event in self.read_all(from_position=position, limit=limit):
            batch.append((stream_id, event))
            position = next_position
        return batch, TailCursor(str(position))

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

    Durable persistence (SQLite/Postgres event stores) implements this.
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

    # Nothing is kept, so nothing can arrive out of order either. Tailing it
    # yields an empty batch forever, which is the honest answer for a store that
    # discarded everything -- `can_replay` is how a caller finds out that
    # silence means "not kept" rather than "nothing new".
    positions_are_commit_ordered: ClassVar[bool] = True

    @property
    def can_replay(self) -> bool:
        """Nothing was kept, so nothing can be read back."""
        return False

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
