"""Event bus for publish/subscribe pattern.

The event bus allows decoupled communication between components via domain events.
Supports optional event persistence via IEventStore.
"""

from collections.abc import Callable
import threading
from typing import Final

from mcp_hangar.domain.contracts.dispatch_checkpoint import IDispatchCheckpoint
from mcp_hangar.domain.contracts.event_bus import IEventBus
from mcp_hangar.domain.contracts.event_store import ConcurrencyError, IEventStore, NullEventStore
from mcp_hangar.domain.contracts.hook_subscriber import IHookSubscriber
from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.domain.value_objects.hook import Hook, HookPhase
from mcp_hangar.lock_hierarchy import LockLevel, TrackedLock
from mcp_hangar.stream_ids import stream_id_for
from mcp_hangar.logging_config import get_logger
from mcp_hangar.metrics import record_error
from mcp_hangar.observability.tracing import get_tracer

logger = get_logger(__name__)

#: `expected_version` meaning "append after whatever is already there".
#:
#: Distinct from -1, which claims the stream does not exist yet and is a real
#: assertion the store will reject if it is wrong. A caller with no version to
#: assert needs a way to say so; without one it would have to invent -1 and get
#: a ConcurrencyError on its second write to the same aggregate.
APPEND_AT_END: Final = -2


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

    def __init__(
        self,
        event_store: IEventStore | None = None,
        dispatch_checkpoint: IDispatchCheckpoint | None = None,
    ):
        """Initialize event bus.

        Args:
            event_store: Optional event store for persistence.
                If None, events are not persisted.
            dispatch_checkpoint: Optional delivery high-water mark. Without one
                the bus behaves exactly as before -- append, deliver, forget --
                which is at-most-once across a crash.
        """
        self._handlers: dict[type[DomainEvent], list[Callable[[DomainEvent], None]]] = {}
        # Lock hierarchy level: EVENT_BUS (20)
        # Safe to acquire after: PROVIDER, PROVIDER_GROUP
        # Safe to acquire before: EVENT_STORE, REPOSITORY, STDIO_CLIENT
        # Note: Handlers are called OUTSIDE this lock to avoid blocking
        self._lock = TrackedLock(LockLevel.EVENT_BUS, "EventBus", reentrant=False)
        self._event_store = event_store or NullEventStore()
        self._dispatch_checkpoint = dispatch_checkpoint
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

    def set_dispatch_checkpoint(self, checkpoint: IDispatchCheckpoint) -> None:
        """Set the delivery high-water mark (late binding during bootstrap).

        Args:
            checkpoint: Durability-matched checkpoint for this bus's store.
        """
        self._dispatch_checkpoint = checkpoint
        logger.info("dispatch_checkpoint_configured", checkpoint_type=type(checkpoint).__name__)

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

    def _resolve_handlers(self, event_class: type[DomainEvent]) -> list[Callable[[DomainEvent], None]]:
        """Handlers for an event class and every event class it inherits from.

        Dispatch used to key on the exact class, which quietly dropped an entire
        family of events: the fifteen deprecated `Provider*` aliases each
        subclass their `McpServer*` counterpart, and every handler in the system
        is registered against the modern class. Publishing a `ProviderStarted`
        therefore reached *zero* handlers -- no error, no warning, just a
        `handlers_count=0` debug line. Replaying an event store written before
        the rename (v1.0.1 and earlier) hit exactly that path.

        Walking the MRO also subsumes the separate `DomainEvent` lookup that
        `subscribe_to_all` relies on, since `DomainEvent` is in every event's
        MRO. It stays last in the returned order, as before, so subscribe-to-all
        handlers keep running after the specific ones.

        A handler registered against two classes in the same MRO is delivered
        once. Registering the same handler twice against one class still
        delivers twice, which is what it did before.

        Caller holds `self._lock`.
        """
        handlers: list[Callable[[DomainEvent], None]] = []
        seen: set[int] = set()
        for cls in event_class.__mro__:
            if cls is DomainEvent or not issubclass(cls, DomainEvent):
                continue
            bucket = self._handlers.get(cls, [])
            handlers.extend(handler for handler in bucket if id(handler) not in seen)
            seen.update(id(handler) for handler in bucket)
        handlers.extend(handler for handler in self._handlers.get(DomainEvent, []) if id(handler) not in seen)
        return handlers

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

        Raises:
            TypeError: If given something that is not a DomainEvent.
        """
        if not isinstance(event, DomainEvent):
            # Fail loudly. `publish([event])` used to be accepted: the list
            # reached every subscribe-to-all handler as a `list` object -- where
            # each one's isinstance checks quietly matched nothing -- while
            # anything subscribed to the event's own type got nothing at all.
            # A publish that silently delivers to no one is worse than a crash.
            raise TypeError(
                f"publish() takes a single DomainEvent, got {type(event).__name__}. "
                "To publish several, call publish() for each."
            )
        event_type_name = event.__class__.__name__
        with self._lock:
            handlers = self._resolve_handlers(type(event))

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
                    # Counted, not only logged. The barrier is right -- one bad
                    # handler must not stop the others -- but until now the only
                    # trace of a swallowed failure was a log line, so an audit
                    # handler that threw on every event looked exactly like one
                    # that was working. A counter puts it on the dashboard the
                    # team already watches.
                    record_error("event_handler", type(e).__name__)
                    logger.exception(
                        "event_handler_error",
                        event_type=event_type_name,
                        handler=getattr(handler, "__qualname__", repr(handler)),
                        error=str(e),
                    )
                    evt_span.record_exception(e)

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

        if expected_version == APPEND_AT_END:
            # No optimistic-concurrency claim: the caller is appending to
            # whatever is there. Aggregates do not carry a stream version yet --
            # their state comes from the config repository, not from replay --
            # so a version they could check against does not exist. Real
            # concurrency control arrives with rehydration, and the callers that
            # want it pass a version explicitly, as they do today.
            expected_version = self._event_store.get_stream_version(stream_id)

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("event_store.append") as store_span:
            store_span.set_attribute("event_store.stream_id", stream_id)
            store_span.set_attribute("event_store.events_count", len(events))
            store_span.set_attribute("event_store.expected_version", expected_version)

            try:
                new_version = self._event_store.append(stream_id, events, expected_version)
            except ConcurrencyError:
                # A genuine conflict is the caller's business, not something to
                # paper over: someone else wrote where this caller expected to.
                raise
            except Exception as e:  # noqa: BLE001 -- see below; the store is not allowed to take delivery down with it
                # Persistence failed for an infrastructure reason -- disk full,
                # database locked, backend gone. Delivering nothing here would
                # be the worse failure: metrics, audit, security and enforcement
                # handlers all run off this path, so a store outage would
                # silently switch off enforcement while the gateway kept serving
                # traffic. Before this change those handlers ran without any
                # store at all, and losing them is a regression persistence must
                # not cause.
                #
                # So: deliver, and say loudly that the record is missing. The
                # events are gone from the log for good -- there is no retry
                # queue in front of a store that just failed.
                logger.error(
                    "event_persistence_failed",
                    stream_id=stream_id,
                    events_count=len(events),
                    error=str(e),
                    detail="events delivered to handlers but NOT persisted; the audit log has a hole here",
                    exc_info=True,
                )
                store_span.record_exception(e)
                for event in events:
                    self.publish(event)
                return expected_version

            store_span.set_attribute("event_store.new_version", new_version)

            logger.debug(
                "events_persisted",
                stream_id=stream_id,
                events_count=len(events),
                new_version=new_version,
            )

        # Then publish to handlers, on this thread, before returning -- the
        # delivery semantics are unchanged, and deliberately so: metrics, audit,
        # security and enforcement handlers are all called inline today, and
        # every caller and test that reads a result after publishing depends on
        # that.
        for event in events:
            self.publish(event)

        # Only now record how far delivery got. The order is the whole point:
        # append, deliver, then mark. A crash before the mark re-delivers on the
        # next sweep instead of losing the events, which is why handlers must be
        # idempotent on `event_id`.
        self._advance_dispatch_checkpoint(len(events))

        return new_version

    def _advance_dispatch_checkpoint(self, appended: int) -> None:
        """Move the delivery high-water mark past the events just handed over.

        The positions come from re-reading the tail rather than from `append`,
        which returns a stream version and knows nothing about global order.

        Concurrency, stated rather than discovered: two threads appending at
        once may each read the other's rows here, so the mark can move past
        events a *different* thread is still delivering. That thread delivers
        them itself, so nothing is missed in the normal case; the exposure is
        narrow and real -- if that other thread dies mid-delivery, its events
        sit below a mark that says they were handled. Closing it needs delivery
        serialized under a lock held across handler calls, which is what
        `publish` deliberately avoids to keep handlers off the bus lock. The
        sweep is a recovery mechanism, not a distributed-log guarantee.
        """
        if self._dispatch_checkpoint is None or not self._event_store.can_replay:
            return
        try:
            start = self._dispatch_checkpoint.read()
            positions = [pos for pos, _, _ in self._event_store.read_all(from_position=start, limit=appended)]
            if positions:
                self._dispatch_checkpoint.advance(max(positions))
        except Exception as e:  # noqa: BLE001 -- fault-barrier: a checkpoint failure must not undo a delivered publish
            # Leaving the mark behind costs a re-delivery on the next sweep.
            # Raising here would fail a publish whose handlers have already run.
            logger.warning("dispatch_checkpoint_advance_failed", error=str(e))

    def dispatch_pending(self) -> int:
        """Deliver everything appended but not yet handed to handlers.

        Called once at startup. The window it closes is real and was open
        forever: `publish_to_stream` commits the append and then calls handlers,
        so a process that died in between left events durably stored and never
        delivered, with nothing that would ever look again.

        Events are re-read from the store, so handlers receive deserialized
        copies rather than the instances an aggregate emitted. That is inherent
        to recovery -- the originals died with the process -- and is the second
        reason handlers must key on `event_id` rather than on identity.

        Returns:
            How many events were delivered.
        """
        if self._dispatch_checkpoint is None or not self._event_store.can_replay:
            return 0

        start = self._dispatch_checkpoint.read()
        delivered = 0
        for position, _stream_id, event in self._event_store.read_all(from_position=start, limit=10_000):
            self.publish(event)
            self._dispatch_checkpoint.advance(position)
            delivered += 1

        if delivered:
            logger.warning(
                "undelivered_events_recovered",
                count=delivered,
                from_position=start,
                detail="events were persisted but never handed to handlers; a previous run ended between the two",
            )
        return delivered

    def publish_aggregate_events(
        self,
        aggregate_type: str,
        aggregate_id: str,
        events: list[DomainEvent],
        expected_version: int = APPEND_AT_END,
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
        stream_id = stream_id_for(aggregate_type, aggregate_id)
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

    def clear(self) -> None:
        """Clear all subscriptions (mainly for testing)."""
        with self._lock:
            self._handlers.clear()
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
