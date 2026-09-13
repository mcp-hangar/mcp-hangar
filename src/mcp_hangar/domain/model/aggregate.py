"""Base class for aggregate roots in the domain model."""

from abc import ABC

from ..events import DomainEvent


class AggregateRoot(ABC):
    """
    Base class for aggregate roots.

    Aggregate roots are the entry points to aggregates and ensure consistency
    within their boundaries. They collect domain events that can be published
    after the aggregate is persisted.
    """

    def __init__(self):
        self._uncommitted_events: list[DomainEvent] = []
        self._version: int = 0

    def _record_event(self, event: DomainEvent) -> None:
        """
        Record a domain event to be published after persistence.

        Events are collected and published as a batch to ensure consistency.
        The event bus should handle failures gracefully to avoid breaking
        the aggregate's core functionality.
        """
        self._uncommitted_events.append(event)

    def collect_events(self) -> list[DomainEvent]:
        """
        Collect and clear pending domain events.

        This should be called after the aggregate is persisted to publish
        events to the event bus.

        Drained one event at a time, not copied and then cleared. One thread
        drains an aggregate while another is still recording on it -- the health
        worker and a recovery saga's scheduled restart, for one -- and an event
        appended between the copy and the clear was cleared without ever being
        returned, so nothing published it (#1400). `list.pop` and `list.append`
        are each atomic, and appends only go on the end, so an event recorded
        mid-drain is returned now or by the next call. No lock: a bare one here
        would sit outside the `TrackedLock` hierarchy.
        """
        events: list[DomainEvent] = []
        while True:
            try:
                events.append(self._uncommitted_events.pop(0))
            except IndexError:
                return events

    def has_uncommitted_events(self) -> bool:
        """Check if there are uncommitted events."""
        return len(self._uncommitted_events) > 0

    @property
    def version(self) -> int:
        """
        Get the aggregate version for optimistic concurrency control.

        Version is incremented after each state change.
        """
        return self._version

    def _increment_version(self) -> None:
        """Increment version after state change."""
        self._version += 1
