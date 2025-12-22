"""Event bus for publish/subscribe pattern.

The event bus allows decoupled communication between components via domain events.
"""

import logging
import threading
from typing import Callable, Dict, List, Type

from mcp_hangar.domain.events import DomainEvent

logger = logging.getLogger(__name__)


class EventHandler:
    """Base class for event handlers."""

    def handle(self, event: DomainEvent) -> None:
        """Handle a domain event."""
        raise NotImplementedError


class EventBus:
    """
    Thread-safe event bus for publishing and subscribing to domain events.

    Supports multiple subscribers per event type.
    Handlers are called synchronously in order of subscription.
    """

    def __init__(self):
        self._handlers: Dict[Type[DomainEvent], List[Callable[[DomainEvent], None]]] = {}
        self._lock = threading.Lock()
        self._error_handlers: List[Callable[[Exception, DomainEvent], None]] = []

    def subscribe(
        self, event_type: Type[DomainEvent], handler: Callable[[DomainEvent], None]
    ) -> None:
        """
        Subscribe to a specific event type.

        Args:
            event_type: The type of event to subscribe to
            handler: Callable that takes the event as parameter
        """
        with self._lock:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)

        logger.debug(f"Subscribed handler to {event_type.__name__}")

    def subscribe_to_all(self, handler: Callable[[DomainEvent], None]) -> None:
        """
        Subscribe to all event types.

        Args:
            handler: Callable that takes any event as parameter
        """
        with self._lock:
            if DomainEvent not in self._handlers:
                self._handlers[DomainEvent] = []
            self._handlers[DomainEvent].append(handler)

        logger.debug("Subscribed handler to all events")

    def unsubscribe(
        self, event_type: Type[DomainEvent], handler: Callable[[DomainEvent], None]
    ) -> None:
        """
        Unsubscribe a handler from an event type.

        Args:
            event_type: The type of event
            handler: The handler to remove
        """
        with self._lock:
            if event_type in self._handlers:
                self._handlers[event_type].remove(handler)

    def publish(self, event: DomainEvent) -> None:
        """
        Publish an event to all subscribed handlers.

        Handlers are called synchronously in subscription order.
        If a handler fails, the exception is logged and remaining handlers
        are still called.

        Args:
            event: The domain event to publish
        """
        with self._lock:
            # Get handlers for this specific event type
            specific_handlers = self._handlers.get(type(event), [])
            # Get handlers subscribed to all events
            all_handlers = self._handlers.get(DomainEvent, [])
            handlers = specific_handlers + all_handlers

        logger.debug(f"Publishing {event.__class__.__name__} to {len(handlers)} handlers")

        # Call handlers outside the lock
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(
                    f"Error in event handler for {event.__class__.__name__}: {e}",
                    exc_info=True,
                )
                # Call error handlers
                for error_handler in self._error_handlers:
                    try:
                        error_handler(e, event)
                    except Exception as eh:
                        logger.error(
                            f"Error in event bus error handler for {event.__class__.__name__}: {eh}",
                            exc_info=True,
                        )

    def on_error(self, handler: Callable[[Exception, DomainEvent], None]) -> None:
        """
        Register a handler for errors that occur during event handling.

        Args:
            handler: Callable that takes (exception, event)
        """
        self._error_handlers.append(handler)

    def clear(self) -> None:
        """Clear all subscriptions (mainly for testing)."""
        with self._lock:
            self._handlers.clear()
            self._error_handlers.clear()


# Global event bus instance
_global_event_bus: EventBus | None = None
_global_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """
    Get the global event bus instance (singleton pattern).

    Returns:
        The global EventBus instance
    """
    global _global_event_bus

    if _global_event_bus is None:
        with _global_bus_lock:
            if _global_event_bus is None:
                _global_event_bus = EventBus()

    return _global_event_bus


def reset_event_bus() -> None:
    """Reset the global event bus (mainly for testing)."""
    global _global_event_bus

    with _global_bus_lock:
        _global_event_bus = None
