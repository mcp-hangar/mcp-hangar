"""In-memory Event Store implementation.

Useful for testing and development. Events are lost on restart.
"""

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field

from mcp_hangar.domain.contracts.event_store import ConcurrencyError, IEventStore
from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class StoredEvent:
    """Event wrapper with metadata."""

    global_position: int
    stream_id: str
    stream_version: int
    event: DomainEvent


@dataclass
class Stream:
    """Stream state tracking."""

    stream_id: str
    version: int = -1
    events: list[StoredEvent] = field(default_factory=list)


class InMemoryEventStore(IEventStore):
    """In-memory event store for testing and development.

    Thread-safe but not persistent. All data is lost on restart.
    """

    def __init__(self):
        """Initialize empty event store."""
        self._streams: dict[str, Stream] = {}
        self._all_events: list[StoredEvent] = []
        self._lock = threading.Lock()
        self._global_position = 0

        logger.info("in_memory_event_store_initialized")

    def append(
        self,
        stream_id: str,
        events: list[DomainEvent],
        expected_version: int,
    ) -> int:
        """Append events with optimistic concurrency."""
        if not events:
            return expected_version

        with self._lock:
            # Get or create stream
            stream = self._streams.get(stream_id)
            if stream is None:
                stream = Stream(stream_id=stream_id)
                self._streams[stream_id] = stream

            # Check version
            if stream.version != expected_version:
                raise ConcurrencyError(stream_id, expected_version, stream.version)

            # Append events
            for event in events:
                self._global_position += 1
                stream.version += 1

                stored = StoredEvent(
                    global_position=self._global_position,
                    stream_id=stream_id,
                    stream_version=stream.version,
                    event=event,
                )
                stream.events.append(stored)
                self._all_events.append(stored)

            logger.debug(
                "events_appended",
                stream_id=stream_id,
                events_count=len(events),
                new_version=stream.version,
            )

            return stream.version

    def read_stream(
        self,
        stream_id: str,
        from_version: int = 0,
    ) -> list[DomainEvent]:
        """Read events from stream."""
        with self._lock:
            stream = self._streams.get(stream_id)
            if stream is None:
                return []

            return [stored.event for stored in stream.events if stored.stream_version >= from_version]

    def read_all(
        self,
        from_position: int = 0,
        limit: int = 1000,
    ) -> Iterator[tuple[int, str, DomainEvent]]:
        """Read all events globally."""
        with self._lock:
            events = [e for e in self._all_events if e.global_position > from_position][:limit]

        for stored in events:
            yield stored.global_position, stored.stream_id, stored.event

    def get_stream_version(self, stream_id: str) -> int:
        """Get current stream version."""
        with self._lock:
            stream = self._streams.get(stream_id)
            return stream.version if stream else -1

    def clear(self) -> None:
        """Clear all events (for testing)."""
        with self._lock:
            self._streams.clear()
            self._all_events.clear()
            self._global_position = 0

        logger.info("event_store_cleared")

    def get_event_count(self) -> int:
        """Get total event count."""
        with self._lock:
            return len(self._all_events)

    def get_stream_count(self) -> int:
        """Get total stream count."""
        with self._lock:
            return len(self._streams)

    def list_streams(self, prefix: str = "") -> list[str]:
        """List all stream IDs, optionally filtered by prefix."""
        with self._lock:
            if prefix:
                return [sid for sid in self._streams.keys() if sid.startswith(prefix)]
            return list(self._streams.keys())
