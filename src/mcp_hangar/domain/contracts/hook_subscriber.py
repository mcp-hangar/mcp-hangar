"""Hook subscriber contract for phase-aware event delivery.

Defines IHookSubscriber so infrastructure can fan out Hook objects
to subscribers without coupling to concrete implementations.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..value_objects.hook import Hook


@runtime_checkable
class IHookSubscriber(Protocol):
    """Subscriber that receives phase-wrapped domain events."""

    @abstractmethod
    def on_hook(self, hook: Hook) -> None:
        """Handle a hook delivery.

        Args:
            hook: Phase-wrapped domain event.
        """
        ...
