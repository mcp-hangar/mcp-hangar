"""Where a saga's progress is kept between events.

A port, because a saga's progress is state the gateway persists, and everything
the gateway persists has to be provided by whichever storage backend is
selected. Without one, `SagaStateStore` was a concrete SQLite class that
application code depended on directly -- which is exactly how a persistence
concern ends up impossible to serve from a second backend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ISagaStateStore(ABC):
    """Port for saga checkpointing and idempotency."""

    @abstractmethod
    def checkpoint(self, saga_type: str, state: dict[str, Any], last_position: int) -> None:
        """Record a saga's state and how far through the log it has read.

        Args:
            saga_type: Identifies the saga; one row per type.
            state: The saga's own state, opaque to the store.
            last_position: Global log position this state reflects.
        """

    @abstractmethod
    def load(self, saga_type: str) -> dict[str, Any] | None:
        """The saga's stored state, or None if it has never checkpointed."""

    @abstractmethod
    def mark_processed(self, saga_type: str, event_position: int) -> None:
        """Record that this saga has handled the event at `event_position`.

        Delivery is at-least-once, so a saga must be able to recognise an event
        it has already acted on.
        """

    @abstractmethod
    def is_processed(self, saga_type: str, event_position: int) -> bool:
        """Whether this saga has already handled the event at `event_position`."""
