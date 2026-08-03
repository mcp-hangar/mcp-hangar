# pyright: reportAny=false, reportExplicitAny=false, reportUnannotatedClassAttribute=false

"""Event serialization for persistence.

Handles conversion of domain events to/from JSON for storage in event store.
"""

from collections.abc import Iterator
import dataclasses
from datetime import datetime
import inspect
import json
from typing import Any

from mcp_hangar.domain.events import LEGACY_EVENT_TYPE_NAMES, DomainEvent, canonical_event_type
from mcp_hangar.logging_config import get_logger

from .event_upcaster import UpcasterChain

logger = get_logger(__name__)


def _iter_event_classes() -> "Iterator[type[DomainEvent]]":
    """Every concrete DomainEvent subclass currently imported."""
    stack: list[type[DomainEvent]] = [DomainEvent]
    seen: set[int] = set()
    while stack:
        for subclass in stack.pop().__subclasses__():
            if id(subclass) in seen:
                continue
            seen.add(id(subclass))
            stack.append(subclass)
            yield subclass


def _refresh_registry() -> int:
    """Register every event class that is imported and not already known.

    Returns the number of newly registered classes.
    """
    added = 0
    for cls in _iter_event_classes():
        name = cls.__name__
        # Deprecated spellings are resolved to their canonical name before
        # lookup, so registering them would only create a second entry that
        # nothing reaches.
        if name in LEGACY_EVENT_TYPE_NAMES:
            continue
        existing = EVENT_TYPE_MAP.get(name)
        if existing is None:
            EVENT_TYPE_MAP[name] = cls
            added += 1
        elif existing is not cls:
            # Two event classes sharing a name means the stored type name is
            # ambiguous and one of them will silently deserialise as the other.
            logger.warning(
                "event_type_name_collision",
                event_type=name,
                registered=f"{existing.__module__}.{existing.__qualname__}",
                ignored=f"{cls.__module__}.{cls.__qualname__}",
            )
    return added


# Anything the serializer can WRITE it must be able to READ. `serialize` accepts
# any DomainEvent -- it just dumps the instance dict -- so a hand-curated table of
# readable types is a table that will disagree with the writer. It did: it listed
# 30 of the 116 event classes in the codebase, which is why every API key written
# under `auth.storage.driver: event_sourcing` was durably stored and permanently
# unreadable, and why the nine group events had the same problem that the
# never-called `register_event_type` helper was added to solve.
#
# Populated from the class hierarchy instead, and refreshed on a lookup miss so a
# class whose module is imported later is still found. That is sound because an
# event can only have been WRITTEN by a process that imported its class, and can
# only be meaningfully READ by one that does too.
EVENT_TYPE_MAP: dict[str, type[DomainEvent]] = {}
_EVENT_CLASS_BY_TYPE: dict[str, type[DomainEvent]] = EVENT_TYPE_MAP
_refresh_registry()

EVENT_VERSION_MAP: dict[str, int] = {
    # McpServer Lifecycle
    "McpServerStarted": 1,
    "McpServerStopped": 1,
    "McpServerDegraded": 1,
    "McpServerStateChanged": 1,
    "McpServerIdleDetected": 1,
    # Circuit Breaker
    "CircuitBreakerStateChanged": 1,
    # Tool Invocation
    "ToolInvocationRequested": 1,
    "ToolInvocationCompleted": 1,
    "ToolInvocationFailed": 1,
    # Health Check
    "HealthCheckPassed": 1,
    "HealthCheckFailed": 1,
    # Discovery
    "McpServerDiscovered": 1,
    "McpServerDiscoveryLost": 1,
    "McpServerDiscoveryConfigChanged": 1,
    "McpServerQuarantined": 1,
    "McpServerApproved": 1,
    "DiscoveryCycleCompleted": 1,
    "DiscoverySourceHealthChanged": 1,
    # Capability enforcement
    "CapabilityViolationDetected": 2,
    "EgressBlocked": 1,
    "EgressPolicyCleared": 1,
    "EgressPolicySet": 1,
    "EgressPolicyViolationObserved": 1,
    "McpServerCapabilityQuarantined": 1,
    "McpServerCapabilityQuarantineReleased": 1,
    # Policy push
    "PolicyPushRejected": 1,
    # Approval Gate
    "ToolApprovalRequested": 1,
    "ToolApprovalGranted": 1,
    "ToolApprovalDenied": 1,
    "ToolApprovalExpired": 1,
}


def get_current_version(event_type: str) -> int:
    """Get current schema version for an event type.

    Args:
        event_type: Domain event type name.

    Returns:
        Current schema version. Defaults to 1.
    """

    return EVENT_VERSION_MAP.get(event_type, 1)


class EventSerializationError(Exception):
    """Raised when event serialization or deserialization fails."""

    def __init__(self, event_type: str, message: str):
        self.event_type = event_type
        super().__init__(f"Failed to serialize/deserialize {event_type}: {message}")


class EventSerializer:
    """Serializes domain events to/from JSON.

    Thread-safe: stateless, can be shared across threads.
    """

    def __init__(self, upcaster_chain: UpcasterChain | None = None) -> None:
        """Create an EventSerializer.

        Args:
            upcaster_chain: Optional upcaster chain used during deserialization.
                If not provided, an empty chain is used.
        """

        self._upcaster_chain = upcaster_chain or UpcasterChain()

    def serialize(self, event: DomainEvent) -> tuple[str, str]:
        """Serialize a domain event to (event_type, json_data).

        Args:
            event: The domain event to serialize.

        Returns:
            Tuple of (event_type_name, json_string).

        Raises:
            EventSerializationError: If serialization fails.
        """
        # Written under the current name even when the caller published one of the
        # deprecated `Provider*` aliases, so the store does not keep accumulating
        # rows that need translating on the way back out.
        event_type = canonical_event_type(type(event).__name__)

        try:
            version = get_current_version(event_type)
            data = {"_version": version, **self._to_dict(event)}
            json_data = json.dumps(data, default=self._json_encoder, ensure_ascii=False)
            return event_type, json_data
        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as EventSerializationError
            logger.error(
                "event_serialization_failed",
                event_type=event_type,
                error=str(e),
            )
            raise EventSerializationError(event_type, str(e)) from e

    def deserialize(self, event_type: str, data: str) -> DomainEvent:
        """Deserialize a domain event from JSON.

        Args:
            event_type: The event type name.
            data: JSON string containing event data.

        Returns:
            Reconstructed domain event.

        Raises:
            EventSerializationError: If deserialization fails.
        """
        # Stores written before the provider -> mcp_server rename (v1.0.1 and
        # earlier) hold rows typed `ProviderStarted`, `ProviderDiscovered` and so
        # on. Resolving to the current name here means such a row reconstructs
        # into the class handlers actually subscribe to, and looks its schema
        # version up under the key the upcasters are registered against --
        # neither of which happened while the alias classes were mapped
        # separately.
        canonical_type = canonical_event_type(event_type)
        event_class = _EVENT_CLASS_BY_TYPE.get(canonical_type)
        if event_class is None:
            # The registry is seeded at import. A miss may just mean the class's
            # module was imported afterwards, so re-scan before giving up rather
            # than making correctness depend on import order.
            if _refresh_registry():
                event_class = _EVENT_CLASS_BY_TYPE.get(canonical_type)
        if event_class is None:
            raise EventSerializationError(
                event_type,
                f"Unknown event type. Known types: {sorted(_EVENT_CLASS_BY_TYPE)}",
            )

        try:
            payload = json.loads(data)

            version = payload.pop("_version", 1)
            current_version = get_current_version(canonical_type)

            if version < current_version:
                version, payload = self._upcaster_chain.upcast(
                    canonical_type,
                    version,
                    payload,
                    current_version=current_version,
                )

            # Ensure we don't pass version through to dataclass ctor
            payload.pop("_version", None)

            return self._from_dict(event_class, payload)
        except json.JSONDecodeError as e:
            raise EventSerializationError(event_type, f"Invalid JSON: {e}") from e
        except Exception as e:  # noqa: BLE001 -- infra-boundary: re-raises as EventSerializationError
            logger.error(
                "event_deserialization_failed",
                event_type=event_type,
                error=str(e),
            )
            raise EventSerializationError(event_type, str(e)) from e

    def _to_dict(self, event: DomainEvent) -> dict[str, Any]:
        """Convert event to dictionary, excluding private attributes."""
        return {key: value for key, value in vars(event).items() if not key.startswith("_")}

    def _restore_datetimes(self, cls: type[DomainEvent], data: dict[str, Any]) -> dict[str, Any]:
        """Parse ISO strings back into datetimes on fields annotated as such.

        JSON has no datetime, so `_json_encoder` writes `isoformat()`. Nothing
        read it back: a `datetime` field went into the store as a datetime and
        came out as a `str`, silently and only on replay, so any consumer doing
        arithmetic or comparison on it broke long after the write.
        """
        if not dataclasses.is_dataclass(cls):
            return data
        for field in dataclasses.fields(cls):
            value = data.get(field.name)
            if not isinstance(value, str) or "datetime" not in str(field.type):
                continue
            try:
                data[field.name] = datetime.fromisoformat(value)
            except ValueError:
                # Leave it alone: a malformed timestamp is better reported by the
                # constructor than swallowed here.
                logger.warning("event_datetime_unparseable", event_type=cls.__name__, field=field.name)
        return data

    def _from_dict(self, cls: type[DomainEvent], data: dict[str, Any]) -> DomainEvent:
        """Reconstruct event from dictionary.

        Identity restoration goes through DomainEvent.rehydrate so the "patch
        event_id after construction" wart lives in one place rather than here
        and in the event-sourced repository.
        """
        event_id = data.pop("event_id", None)
        occurred_at = data.pop("occurred_at", None)

        # Event dataclasses have different constructor signatures; we instantiate
        # dynamically from the payload. DomainEvent.rehydrate restores the stored
        # identity -- replay must not mint a new event_id or re-date the event.
        ctor_kwargs = self._filter_constructor_kwargs(cls, self._restore_datetimes(cls, data))
        return cls.rehydrate(event_id, occurred_at, **ctor_kwargs)

    def _filter_constructor_kwargs(self, cls: type[DomainEvent], data: dict[str, Any]) -> dict[str, Any]:
        """Filter payload keys to those accepted by the event constructor.

        This allows forward-compatible payloads with extra fields introduced in newer schema versions.

        Args:
            cls: Event class.
            data: Payload dict.

        Returns:
            Dict containing only keys that are valid __init__ parameters.
        """

        try:
            sig = inspect.signature(cls)
        except (TypeError, ValueError):
            # Fallback: best-effort passthrough.
            return data

        params = list(sig.parameters.values())
        accepted: set[str] = {
            p.name
            for p in params
            if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }

        # If constructor takes **kwargs, avoid filtering.
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
            return data

        return {k: v for k, v in data.items() if k in accepted}

    def _json_encoder(self, obj: Any) -> Any:
        """Custom JSON encoder for non-standard types."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def register_event_type(event_class: type[DomainEvent]) -> None:
    """Register an event type for deserialization.

    Rarely needed now: any imported `DomainEvent` subclass registers itself, so
    this is only for a class that must be readable before its module has been
    imported anywhere. It was previously the only way to make the group events
    readable, and was never actually called -- which is precisely how they ended
    up writable but not readable.

    Args:
        event_class: The event class to register.
    """
    event_type = event_class.__name__
    EVENT_TYPE_MAP[event_type] = event_class
    logger.debug("event_type_registered", event_type=event_type)
