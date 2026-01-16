"""Event serialization for persistence.

Handles conversion of domain events to/from JSON for storage in event store.
"""

from datetime import datetime
import inspect
import json
from typing import Any

from mcp_hangar.domain.events import (
    DiscoveryCycleCompleted,
    DiscoverySourceHealthChanged,
    DomainEvent,
    HealthCheckFailed,
    HealthCheckPassed,
    ProviderApproved,
    ProviderDegraded,
    ProviderDiscovered,
    ProviderDiscoveryConfigChanged,
    ProviderDiscoveryLost,
    ProviderIdleDetected,
    ProviderQuarantined,
    ProviderStarted,
    ProviderStateChanged,
    ProviderStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from mcp_hangar.logging_config import get_logger

from .event_upcaster import UpcasterChain

logger = get_logger(__name__)

# Registry of event types for deserialization
EVENT_TYPE_MAP: dict[str, type[DomainEvent]] = {
    # Provider Lifecycle
    "ProviderStarted": ProviderStarted,
    "ProviderStopped": ProviderStopped,
    "ProviderDegraded": ProviderDegraded,
    "ProviderStateChanged": ProviderStateChanged,
    "ProviderIdleDetected": ProviderIdleDetected,
    # Tool Invocation
    "ToolInvocationRequested": ToolInvocationRequested,
    "ToolInvocationCompleted": ToolInvocationCompleted,
    "ToolInvocationFailed": ToolInvocationFailed,
    # Health Check
    "HealthCheckPassed": HealthCheckPassed,
    "HealthCheckFailed": HealthCheckFailed,
    # Discovery
    "ProviderDiscovered": ProviderDiscovered,
    "ProviderDiscoveryLost": ProviderDiscoveryLost,
    "ProviderDiscoveryConfigChanged": ProviderDiscoveryConfigChanged,
    "ProviderQuarantined": ProviderQuarantined,
    "ProviderApproved": ProviderApproved,
    "DiscoveryCycleCompleted": DiscoveryCycleCompleted,
    "DiscoverySourceHealthChanged": DiscoverySourceHealthChanged,
}

EVENT_VERSION_MAP: dict[str, int] = {
    # Provider Lifecycle
    "ProviderStarted": 1,
    "ProviderStopped": 1,
    "ProviderDegraded": 1,
    "ProviderStateChanged": 1,
    "ProviderIdleDetected": 1,
    # Tool Invocation
    "ToolInvocationRequested": 1,
    "ToolInvocationCompleted": 1,
    "ToolInvocationFailed": 1,
    # Health Check
    "HealthCheckPassed": 1,
    "HealthCheckFailed": 1,
    # Discovery
    "ProviderDiscovered": 1,
    "ProviderDiscoveryLost": 1,
    "ProviderDiscoveryConfigChanged": 1,
    "ProviderQuarantined": 1,
    "ProviderApproved": 1,
    "DiscoveryCycleCompleted": 1,
    "DiscoverySourceHealthChanged": 1,
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
        event_type = type(event).__name__

        try:
            version = get_current_version(event_type)
            data = {"_version": version, **self._to_dict(event)}
            json_data = json.dumps(data, default=self._json_encoder, ensure_ascii=False)
            return event_type, json_data
        except Exception as e:
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
        event_class = EVENT_TYPE_MAP.get(event_type)
        if not event_class:
            raise EventSerializationError(
                event_type,
                f"Unknown event type. Known types: {list(EVENT_TYPE_MAP.keys())}",
            )

        try:
            payload = json.loads(data)

            version = payload.pop("_version", 1)
            current_version = get_current_version(event_type)

            if version < current_version:
                version, payload = self._upcaster_chain.upcast(
                    event_type,
                    version,
                    payload,
                    current_version=current_version,
                )

            # Ensure we don't pass version through to dataclass ctor
            payload.pop("_version", None)

            return self._from_dict(event_class, payload)
        except json.JSONDecodeError as e:
            raise EventSerializationError(event_type, f"Invalid JSON: {e}") from e
        except Exception as e:
            logger.error(
                "event_deserialization_failed",
                event_type=event_type,
                error=str(e),
            )
            raise EventSerializationError(event_type, str(e)) from e

    def _to_dict(self, event: DomainEvent) -> dict[str, Any]:
        """Convert event to dictionary, excluding private attributes."""
        return {key: value for key, value in vars(event).items() if not key.startswith("_")}

    def _from_dict(self, cls: type[DomainEvent], data: dict[str, Any]) -> DomainEvent:
        """Reconstruct event from dictionary.

        Handles the special case of DomainEvent base class initialization
        by pre-setting event_id and occurred_at if present in data.
        """
        # Extract base class fields
        event_id = data.pop("event_id", None)
        occurred_at = data.pop("occurred_at", None)

        ctor_kwargs = self._filter_constructor_kwargs(cls, data)

        # Create instance with remaining data.
        # Event dataclasses have different constructor signatures; we instantiate dynamically from payload.
        instance = cls(**ctor_kwargs)  # type: ignore[call-arg]

        # Restore original values if present
        if event_id is not None:
            instance.event_id = event_id
        if occurred_at is not None:
            instance.occurred_at = occurred_at

        return instance

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
    """Register a custom event type for deserialization.

    Use this to register event types from other modules (e.g., provider_group events).

    Args:
        event_class: The event class to register.
    """
    event_type = event_class.__name__
    EVENT_TYPE_MAP[event_type] = event_class
    logger.debug("event_type_registered", event_type=event_type)
