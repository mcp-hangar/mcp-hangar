"""Event bus contract - interface for publishing domain events.

Defines IEventBus so application layer can publish events without
depending on infrastructure.EventBus directly.
"""

from abc import ABC, abstractmethod
from enum import Enum

from ..events import DomainEvent


class HandlerKind(Enum):
    """What a handler does with an event, which decides where it may run.

    Declared at subscription and required there. A default would have to be
    wrong for half the handlers, and both wrong answers are silent: an
    unclassified effect exports the same tool call from three replicas, and an
    unclassified projection leaves two of them with a stale view.

    The question this answers only exists once a replica sees events it did not
    produce. One gateway delivers everything to everything and neither kind is
    distinguishable from the other -- which is why the classification lands
    *before* the tailer that makes it matter, rather than after.
    """

    PROJECTION = "projection"
    """Keeps a local view of something. Runs on every replica, for every event,
    whoever produced it -- that is the whole point: a tool catalogue, a risk
    score or a live event feed that only knows about the work one replica
    happened to do is a view of a third of the system. Must be idempotent on
    `event_id` (ADR-018) and must not publish: an event raised while applying a
    tailed event would be tailed in turn, on every replica, forever."""

    EFFECT = "effect"
    """Does something to the world outside this process -- exports to a SIEM,
    charges a budget, sends an alert, takes an enforcement action. Runs **only
    on the instance that produced the event**, which is exactly-once by
    construction because a tool call happens on exactly one replica (#790,
    phase 0.4). Running these on tailed events is how three replicas send three
    copies of every audit record."""


class IEventBus(ABC):
    """Interface for publishing domain events.

    Application layer depends on this interface, not on the concrete EventBus.
    """

    @abstractmethod
    def publish(self, event: DomainEvent) -> None:
        """Publish a domain event to all subscribers.

        Args:
            event: The domain event to publish.
        """

    @abstractmethod
    def publish_aggregate_events(
        self,
        aggregate_type: str,
        aggregate_id: str,
        events: list[DomainEvent],
    ) -> int:
        """Append an aggregate's events to its stream, then publish them.

        This is on the port, not just on the concrete bus, because it is what
        the application layer actually needs of a bus once events are recorded
        rather than only announced: the drain point at the end of a command has
        to say "these belong to this aggregate", and only the bus knows where
        that stream is or how to keep the two halves in step.

        `publish` remains for events that belong to no aggregate stream.

        Args:
            aggregate_type: Aggregate type, e.g. `stream_ids.MCP_SERVER`.
            aggregate_id: The aggregate's own identifier.
            events: Events collected from the aggregate. An empty list is a
                no-op.

        Returns:
            The stream version after the append.
        """
