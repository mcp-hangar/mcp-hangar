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
