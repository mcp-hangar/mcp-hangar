"""Event Store for persisting domain events.

Provides append-only storage of domain events with optimistic concurrency control.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path
import time
from typing import Any

from ..domain.events import DomainEvent
from ..logging_config import get_logger
from .lock_hierarchy import LockLevel, TrackedLock

logger = get_logger(__name__)


@dataclass
class StoredEvent:
    """Wrapper for persisted event with metadata."""

    stream_id: str
    version: int
    event_type: str
    event_id: str
    occurred_at: float
    data: dict[str, Any]
    stored_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "stream_id": self.stream_id,
            "version": self.version,
            "event_type": self.event_type,
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "data": self.data,
            "stored_at": self.stored_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StoredEvent":
        """Create from dictionary."""
        return cls(
            stream_id=d["stream_id"],
            version=d["version"],
            event_type=d["event_type"],
            event_id=d["event_id"],
            occurred_at=d["occurred_at"],
            data=d["data"],
            stored_at=d.get("stored_at", time.time()),
        )


class ConcurrencyError(Exception):
    """Raised when optimistic concurrency check fails."""

    def __init__(self, stream_id: str, expected_version: int, actual_version: int):
        self.stream_id = stream_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Concurrency conflict on stream '{stream_id}': "
            f"expected version {expected_version}, actual {actual_version}"
        )


class StreamNotFoundError(Exception):
    """Raised when event stream doesn't exist."""

    def __init__(self, stream_id: str):
        self.stream_id = stream_id
        super().__init__(f"Stream '{stream_id}' not found")


class EventStore(ABC):
    """Abstract interface for event storage."""

    @abstractmethod
    def append(self, stream_id: str, events: list[DomainEvent], expected_version: int) -> int:
        """
        Append events to a stream with optimistic concurrency.

        Args:
            stream_id: Identifier for the event stream
            events: List of domain events to append
            expected_version: Expected current version (-1 for new stream)

        Returns:
            New version after append

        Raises:
            ConcurrencyError: If expected_version doesn't match actual version
        """
        pass

    @abstractmethod
    def load(self, stream_id: str, from_version: int = 0, to_version: int | None = None) -> list[StoredEvent]:
        """
        Load events from a stream.

        Args:
            stream_id: Identifier for the event stream
            from_version: Start version (inclusive)
            to_version: End version (inclusive), None for all

        Returns:
            List of stored events in order
        """
        pass

    @abstractmethod
    def get_version(self, stream_id: str) -> int:
        """
        Get current version of a stream.

        Returns:
            Current version, or -1 if stream doesn't exist
        """
        pass

    @abstractmethod
    def get_all_stream_ids(self) -> list[str]:
        """Get all stream IDs in the store."""
        pass

    @abstractmethod
    def stream_exists(self, stream_id: str) -> bool:
        """Check if a stream exists."""
        pass


class InMemoryEventStore(EventStore):
    """In-memory event store for testing and development."""

    def __init__(self):
        self._streams: dict[str, list[StoredEvent]] = {}
        # Lock hierarchy level: EVENT_STORE (30)
        # Safe to acquire after: PROVIDER, EVENT_BUS
        # Safe to acquire before: SAGA_MANAGER, STDIO_CLIENT
        # Note: Reentrant because internal methods like get_version() are called under lock
        self._lock = TrackedLock(LockLevel.EVENT_STORE, "InMemoryEventStore", reentrant=True)
        self._subscribers: list[Callable[[StoredEvent], None]] = []

    def append(self, stream_id: str, events: list[DomainEvent], expected_version: int) -> int:
        """Append events with optimistic concurrency."""
        stored_events: list[StoredEvent] = []

        with self._lock:
            current_version = self.get_version(stream_id)

            if expected_version != current_version:
                raise ConcurrencyError(stream_id, expected_version, current_version)

            if stream_id not in self._streams:
                self._streams[stream_id] = []

            stream = self._streams[stream_id]
            new_version = current_version

            for event in events:
                new_version += 1
                stored = StoredEvent(
                    stream_id=stream_id,
                    version=new_version,
                    event_type=type(event).__name__,
                    event_id=event.event_id,
                    occurred_at=event.occurred_at,
                    data=event.to_dict(),
                )
                stream.append(stored)
                stored_events.append(stored)

            # Copy subscribers list under lock
            subscribers = list(self._subscribers)

        # Notify subscribers OUTSIDE lock to avoid blocking/deadlocks
        for stored in stored_events:
            for subscriber in subscribers:
                try:
                    subscriber(stored)
                except Exception as e:
                    logger.error(f"Event subscriber error: {e}")

        return new_version

    def load(self, stream_id: str, from_version: int = 0, to_version: int | None = None) -> list[StoredEvent]:
        """Load events from a stream."""
        with self._lock:
            if stream_id not in self._streams:
                return []

            stream = self._streams[stream_id]

            # Filter by version range
            result = []
            for event in stream:
                if event.version < from_version:
                    continue
                if to_version is not None and event.version > to_version:
                    break
                result.append(event)

            return result

    def get_version(self, stream_id: str) -> int:
        """Get current version of a stream."""
        with self._lock:
            if stream_id not in self._streams:
                return -1
            stream = self._streams[stream_id]
            return stream[-1].version if stream else -1

    def get_all_stream_ids(self) -> list[str]:
        """Get all stream IDs."""
        with self._lock:
            return list(self._streams.keys())

    def stream_exists(self, stream_id: str) -> bool:
        """Check if stream exists."""
        with self._lock:
            return stream_id in self._streams

    def subscribe(self, callback: Callable[[StoredEvent], None]) -> None:
        """Subscribe to new events."""
        self._subscribers.append(callback)

    def clear(self) -> None:
        """Clear all streams (for testing)."""
        with self._lock:
            self._streams.clear()

    @property
    def total_events(self) -> int:
        """Get total number of events across all streams."""
        with self._lock:
            return sum(len(stream) for stream in self._streams.values())


class FileEventStore(EventStore):
    """File-based event store for persistence."""

    def __init__(self, storage_path: str):
        self._storage_path = Path(storage_path)
        self._storage_path.mkdir(parents=True, exist_ok=True)
        # Lock hierarchy level: EVENT_STORE (30)
        # Safe to acquire after: PROVIDER, EVENT_BUS
        # Safe to acquire before: SAGA_MANAGER, STDIO_CLIENT
        # Note: Reentrant because internal methods like load() are called under lock
        self._lock = TrackedLock(LockLevel.EVENT_STORE, "FileEventStore", reentrant=True)
        self._cache: dict[str, list[StoredEvent]] = {}

    def _stream_file(self, stream_id: str) -> Path:
        """Get file path for a stream."""
        # Sanitize stream_id for filesystem
        safe_id = stream_id.replace("/", "_").replace("\\", "_")
        return self._storage_path / f"{safe_id}.jsonl"

    def append(self, stream_id: str, events: list[DomainEvent], expected_version: int) -> int:
        """Append events with optimistic concurrency."""
        with self._lock:
            current_version = self.get_version(stream_id)

            if expected_version != current_version:
                raise ConcurrencyError(stream_id, expected_version, current_version)

            stream_file = self._stream_file(stream_id)
            new_version = current_version

            # Initialize cache if needed
            if stream_id not in self._cache:
                self._cache[stream_id] = self.load(stream_id)

            with open(stream_file, "a") as f:
                for event in events:
                    new_version += 1
                    stored = StoredEvent(
                        stream_id=stream_id,
                        version=new_version,
                        event_type=type(event).__name__,
                        event_id=event.event_id,
                        occurred_at=event.occurred_at,
                        data=event.to_dict(),
                    )
                    f.write(json.dumps(stored.to_dict()) + "\n")
                    self._cache[stream_id].append(stored)

            return new_version

    def load(self, stream_id: str, from_version: int = 0, to_version: int | None = None) -> list[StoredEvent]:
        """Load events from a stream."""
        with self._lock:
            # Check cache first
            if stream_id in self._cache:
                cached = self._cache[stream_id]
                return [
                    e for e in cached if e.version >= from_version and (to_version is None or e.version <= to_version)
                ]

            # Load from file
            stream_file = self._stream_file(stream_id)
            if not stream_file.exists():
                return []

            events = []
            with open(stream_file) as f:
                for line in f:
                    if line.strip():
                        event = StoredEvent.from_dict(json.loads(line))
                        if event.version >= from_version:
                            if to_version is not None and event.version > to_version:
                                break
                            events.append(event)

            # Cache for future reads
            self._cache[stream_id] = events

            return events

    def get_version(self, stream_id: str) -> int:
        """Get current version of a stream."""
        events = self.load(stream_id)
        return events[-1].version if events else -1

    def get_all_stream_ids(self) -> list[str]:
        """Get all stream IDs."""
        with self._lock:
            stream_ids = []
            for file in self._storage_path.glob("*.jsonl"):
                stream_id = file.stem.replace("_", "/")
                stream_ids.append(stream_id)
            return stream_ids

    def stream_exists(self, stream_id: str) -> bool:
        """Check if stream exists."""
        return self._stream_file(stream_id).exists()

    def clear(self) -> None:
        """Clear all streams (for testing)."""
        with self._lock:
            for file in self._storage_path.glob("*.jsonl"):
                file.unlink()
            self._cache.clear()


class EventStoreSnapshot:
    """Manages snapshots for event-sourced aggregates."""

    def __init__(self, storage_path: str, snapshot_interval: int = 100):
        self._storage_path = Path(storage_path)
        self._storage_path.mkdir(parents=True, exist_ok=True)
        self._snapshot_interval = snapshot_interval
        # Lock hierarchy level: EVENT_STORE (30)
        # Safe to acquire after: PROVIDER, EVENT_BUS
        # Safe to acquire before: SAGA_MANAGER, STDIO_CLIENT
        self._lock = TrackedLock(LockLevel.EVENT_STORE, "EventStoreSnapshot")

    def _snapshot_file(self, stream_id: str) -> Path:
        """Get file path for a snapshot."""
        safe_id = stream_id.replace("/", "_").replace("\\", "_")
        return self._storage_path / f"{safe_id}.snapshot.json"

    def save_snapshot(self, stream_id: str, version: int, state: dict[str, Any]) -> None:
        """Save a snapshot of aggregate state."""
        with self._lock:
            snapshot = {
                "stream_id": stream_id,
                "version": version,
                "state": state,
                "created_at": time.time(),
            }
            with open(self._snapshot_file(stream_id), "w") as f:
                json.dump(snapshot, f)

    def load_snapshot(self, stream_id: str) -> dict[str, Any] | None:
        """Load the latest snapshot for a stream."""
        with self._lock:
            snapshot_file = self._snapshot_file(stream_id)
            if not snapshot_file.exists():
                return None

            with open(snapshot_file) as f:
                return json.load(f)

    def should_snapshot(self, events_since_snapshot: int) -> bool:
        """Determine if a snapshot should be taken."""
        return events_since_snapshot >= self._snapshot_interval

    def clear(self) -> None:
        """Clear all snapshots (for testing)."""
        with self._lock:
            for file in self._storage_path.glob("*.snapshot.json"):
                file.unlink()


# Singleton instance
_event_store: EventStore | None = None


def get_event_store() -> EventStore:
    """Get the global event store instance."""
    global _event_store
    if _event_store is None:
        _event_store = InMemoryEventStore()
    return _event_store


def set_event_store(store: EventStore) -> None:
    """Set the global event store instance."""
    global _event_store
    _event_store = store
