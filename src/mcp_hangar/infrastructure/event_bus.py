"""Event bus for publish/subscribe pattern.

The event bus allows decoupled communication between components via domain events.
Supports optional event persistence via IEventStore.
"""

from collections.abc import Callable
import threading

from mcp_hangar.domain.contracts.event_bus import IEventBus
from mcp_hangar.domain.contracts.event_store import IEventStore, NullEventStore
from mcp_hangar.domain.contracts.hook_subscriber import IHookSubscriber
from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.domain.value_objects.hook import Hook, HookPhase
from mcp_hangar.infrastructure.lock_hierarchy import LockLevel, TrackedLock
from mcp_hangar.logging_config import get_logger
from mcp_hangar.observability.tracing import get_tracer

logger = get_logger(__name__)


class EventHandler:
    """Base class for event handlers."""

    def handle(self, event: DomainEvent) -> None:
        """Handle a domain event."""
        raise NotImplementedError


class EventBus(IEventBus):
    """
    Thread-safe event bus for publishing and subscribing to domain events.

    Supports multiple subscribers per event type.
    Handlers are called synchronously in order of subscription.
    Optionally persists events via IEventStore before publishing.
    """

    def __init__(self, event_store: IEventStore | None = None):
        """Initialize event bus.

        Args:
            event_store: Optional event store for persistence.
                If None, events are not persisted.
        """
        self._handlers: dict[type[DomainEvent], list[Callable[[DomainEvent], None]]] = {}
        # Lock hierarchy level: EVENT_BUS (20)
        # Safe to acquire after: PROVIDER, PROVIDER_GROUP
        # Safe to acquire before: EVENT_STORE, REPOSITORY, STDIO_CLIENT
        # Note: Handlers are called OUTSIDE this lock to avoid blocking
        self._lock = TrackedLock(LockLevel.EVENT_BUS, "EventBus", reentrant=False)
        self._error_handlers: list[Callable[[Exception, DomainEvent], None]] = []
        self._event_store = event_store or NullEventStore()
        self._hook_subscribers: list[IHookSubscriber] = []
        self._hook_sequence: int = 0

    @property
    def event_store(self) -> IEventStore:
        """Get the event store instance."""
        return self._event_store

    def set_event_store(self, event_store: IEventStore) -> None:
        """Set the event store (for late binding during bootstrap).

        Args:
            event_store: Event store implementation.
        """
        self._event_store = event_store
        logger.info("event_store_configured", store_type=type(event_store).__name__)

    def subscribe(self, event_type: type[DomainEvent], handler: Callable[[DomainEvent], None]) -> None:
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

    def unsubscribe_from_all(self, handler: Callable[[DomainEvent], None]) -> None:
        """Unsubscribe a handler that was registered via subscribe_to_all.

        Silently ignores handlers that are not currently registered.

        Args:
            handler: The handler to remove.
        """
        with self._lock:
            if DomainEvent in self._handlers:
                try:
                    self._handlers[DomainEvent].remove(handler)
                except ValueError:
                    pass  # handler not registered -- silently ignore

    def unsubscribe(self, event_type: type[DomainEvent], handler: Callable[[DomainEvent], None]) -> None:
        """
        Unsubscribe a handler from an event type.

        Args:
            event_type: The type of event
            handler: The handler to remove
        """
        with self._lock:
            if event_type in self._handlers:
                self._handlers[event_type].remove(handler)

    def subscribe_hooks(self, subscriber: IHookSubscriber) -> None:
        """Register a hook subscriber for phase-wrapped event delivery.

        Hook subscribers receive Hook objects (event + phase) in parallel
        with the existing flat-event path. This is the forward-looking API;
        flat-event subscribers will be deprecated over time.

        Args:
            subscriber: Hook subscriber to register.
        """
        with self._lock:
            self._hook_subscribers.append(subscriber)
        logger.debug("hook_subscriber_registered", subscriber=type(subscriber).__name__)

    def unsubscribe_hooks(self, subscriber: IHookSubscriber) -> None:
        """Remove a hook subscriber. Silently ignores unregistered subscribers.

        Args:
            subscriber: Hook subscriber to remove.
        """
        with self._lock:
            try:
                self._hook_subscribers.remove(subscriber)
            except ValueError:
                pass

    def publish(self, event: DomainEvent) -> None:
        """
        Publish an event to all subscribed handlers.

        Handlers are called synchronously in subscription order.
        If a handler fails, the exception is logged and remaining handlers
        are still called.

        Note: This method does NOT persist events. Use publish_to_stream()
        for event persistence.

        Args:
            event: The domain event to publish
        """
        event_type_name = event.__class__.__name__
        with self._lock:
            # Get handlers for this specific event type
            specific_handlers = self._handlers.get(type(event), [])
            # Get handlers subscribed to all events
            all_handlers = self._handlers.get(DomainEvent, [])
            handlers = specific_handlers + all_handlers

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span(f"event.publish.{event_type_name}") as evt_span:
            evt_span.set_attribute("event.type", event_type_name)
            evt_span.set_attribute("event.handlers_count", len(handlers))

            logger.debug(
                "event_publishing",
                event_type=event_type_name,
                handlers_count=len(handlers),
            )

            # Call handlers outside the lock
            for handler in handlers:
                try:
                    handler(event)
                except Exception as e:  # noqa: BLE001 -- fault-barrier: handler errors must not break other handlers
                    logger.exception(
                        "event_handler_error",
                        event_type=event_type_name,
                        error=str(e),
                    )
                    evt_span.record_exception(e)
                    # Call error handlers
                    for error_handler in self._error_handlers:
                        try:
                            error_handler(e, event)
                        except Exception as eh:  # noqa: BLE001 -- fault-barrier: error_handler failure must not break event publishing
                            logger.exception(
                                "event_error_handler_failed",
                                event_type=event_type_name,
                                error=str(eh),
                            )

            # Hook fan-out: deliver phase-wrapped event to hook subscribers.
            # Default phase is OBSERVE for events published via the flat API;
            # phase-specific emission will be added when interceptor pipeline
            # code lands (issue #121).
            self._publish_hook(event, HookPhase.OBSERVE, evt_span)

    def publish_to_stream(
        self,
        stream_id: str,
        events: list[DomainEvent],
        expected_version: int = -1,
    ) -> int:
        """
        Persist events to a stream and then publish to handlers.

        This method provides full Event Sourcing support:
        1. Persists events to the event store (with optimistic concurrency)
        2. Publishes events to subscribed handlers

        Args:
            stream_id: Stream identifier (e.g., "mcp_server:math")
            events: List of events to persist and publish
            expected_version: Expected stream version for concurrency check.
                Use -1 for new streams.

        Returns:
            New stream version after append.

        Raises:
            ConcurrencyError: If version mismatch in event store.
        """
        if not events:
            return expected_version

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("event_store.append") as store_span:
            store_span.set_attribute("event_store.stream_id", stream_id)
            store_span.set_attribute("event_store.events_count", len(events))
            store_span.set_attribute("event_store.expected_version", expected_version)

            # Persist first (fail fast if concurrency error)
            new_version = self._event_store.append(stream_id, events, expected_version)
            store_span.set_attribute("event_store.new_version", new_version)

            logger.debug(
                "events_persisted",
                stream_id=stream_id,
                events_count=len(events),
                new_version=new_version,
            )

        # Then publish to handlers
        for event in events:
            self.publish(event)

        return new_version

    def publish_aggregate_events(
        self,
        aggregate_type: str,
        aggregate_id: str,
        events: list[DomainEvent],
        expected_version: int = -1,
    ) -> int:
        """
        Convenience method for publishing aggregate events.

        Constructs stream_id from aggregate type and ID.

        Args:
            aggregate_type: Type of aggregate (e.g., "mcp_server", "mcp_server_group")
            aggregate_id: Unique identifier of the aggregate
            events: Events collected from aggregate
            expected_version: Expected version for concurrency

        Returns:
            New stream version.
        """
        stream_id = f"{aggregate_type}:{aggregate_id}"
        return self.publish_to_stream(stream_id, events, expected_version)

    def publish_hook(self, event: DomainEvent, phase: HookPhase) -> None:
        """Deliver a phase-tagged hook to hook subscribers (MCP PR #2624).

        Unlike :meth:`publish`, this does NOT run flat event handlers or
        persist the event; it delivers a single ``Hook`` carrying the given
        wire-level phase (``HookPhase.REQUEST`` / ``HookPhase.RESPONSE``).
        This is the phase-aware delivery path used by ``interceptor/invoke``,
        letting subscribers observe both the request and response legs.

        Args:
            event: The domain event to wrap.
            phase: Wire-level phase for this delivery.
        """
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span(f"event.publish_hook.{phase.value}") as span:
            span.set_attribute("event.type", event.__class__.__name__)
            span.set_attribute("hook.phase", phase.value)
            self._publish_hook(event, phase, span)

    def _publish_hook(self, event: DomainEvent, phase: HookPhase, span: object) -> None:
        with self._lock:
            subscribers = list(self._hook_subscribers)
            seq = self._hook_sequence
            self._hook_sequence += 1

        if not subscribers:
            return

        hook = Hook(event=event, phase=phase, sequence_number=seq)
        for subscriber in subscribers:
            try:
                subscriber.on_hook(hook)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: hook subscriber errors must not break event publishing
                logger.exception(
                    "hook_subscriber_error",
                    event_type=event.__class__.__name__,
                    phase=phase.value,
                    subscriber=type(subscriber).__name__,
                    error=str(e),
                )
                if hasattr(span, "record_exception"):
                    span.record_exception(e)

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
            self._hook_subscribers.clear()
            self._hook_sequence = 0


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
