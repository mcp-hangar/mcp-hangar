"""Event bus contract - interface for publishing domain events.

Defines IEventBus so application layer can publish events without
depending on infrastructure.EventBus directly.
"""

from abc import ABC, abstractmethod

from ..events import DomainEvent


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
