"""How far the event log has been handed to handlers.

`publish_to_stream` appends events and then calls handlers. Between those two
steps the process can die, and until now that lost the delivery outright: the
events were durably in the store, no handler had seen them, and nothing ever
looked again. At-most-once, on the path we describe publicly as an audit trail.

The fix does not need an outbox table. `events` already carries
`global_position INTEGER PRIMARY KEY AUTOINCREMENT` and the store already reads
by it, so the log *is* the outbox and the dual-write problem an outbox table
exists to solve does not arise here. What was missing is one durable number:
how far along that log delivery has actually got.

The contract is at-least-once. A position is advanced only after handlers have
been called, so a crash re-delivers rather than skips, and **handlers must be
idempotent on `event_id`**.
"""

from abc import ABC, abstractmethod


class IDispatchCheckpoint(ABC):
    """The high-water mark of event delivery, durable across restarts."""

    @abstractmethod
    def read(self) -> int:
        """The last global position handed to handlers.

        Returns:
            The position, or 0 when nothing has been dispatched yet. Positions
            are exclusive lower bounds, matching `IEventStore.read_all`.
        """

    @abstractmethod
    def advance(self, position: int) -> None:
        """Record that delivery has reached `position`.

        Never moves backwards: a caller that has just delivered an older batch
        must not undo a newer one. Implementations keep the maximum.

        Args:
            position: Global position of the last event handed to handlers.
        """
