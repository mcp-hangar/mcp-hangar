"""IProviderLogBuffer contract for the domain layer.

Defines the interface for per-provider log ring buffers.  Implementations
live in the infrastructure layer.
"""

from abc import ABC, abstractmethod

from ..value_objects.log import LogLine


class IProviderLogBuffer(ABC):
    """Interface for a per-provider log ring buffer.

    Captures stdout and stderr lines emitted by a running provider process
    and exposes them for REST and WebSocket consumers.

    Implementations must be thread-safe -- reader threads and HTTP handlers
    access the buffer concurrently.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Identifier of the provider this buffer belongs to."""

    @abstractmethod
    def append(self, line: LogLine) -> None:
        """Add a log line to the buffer.

        When the buffer is at capacity the oldest line is silently discarded
        (ring-buffer semantics).

        Args:
            line: The log line to store.
        """

    @abstractmethod
    def tail(self, n: int) -> list[LogLine]:
        """Return the most recent *n* log lines, oldest first.

        Args:
            n: Maximum number of lines to return.  When fewer than *n* lines
               are stored, all stored lines are returned.

        Returns:
            List of :class:`LogLine` in chronological order.
        """

    @abstractmethod
    def clear(self) -> None:
        """Remove all stored log lines from the buffer."""
