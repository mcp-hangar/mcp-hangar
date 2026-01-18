"""Event Sourced Repository implementation.

Stores providers by persisting their domain events and rebuilding state on load.
"""

import threading
from typing import Any

from ..domain.events import DomainEvent
from ..domain.model.event_sourced_provider import EventSourcedProvider, ProviderSnapshot
from ..domain.repository import IProviderRepository, ProviderLike
from ..logging_config import get_logger
from .event_bus import EventBus, get_event_bus
from .event_store import EventStore, EventStoreSnapshot, StoredEvent, get_event_store

logger = get_logger(__name__)


class ProviderConfigStore:
    """Stores provider configuration (command, image, env, etc.)"""

    def __init__(self):
        self._configs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def save(self, provider_id: str, config: dict[str, Any]) -> None:
        """Save provider configuration."""
        with self._lock:
            self._configs[provider_id] = dict(config)

    def load(self, provider_id: str) -> dict[str, Any] | None:
        """Load provider configuration."""
        with self._lock:
            if provider_id in self._configs:
                return dict(self._configs[provider_id])
            return None

    def remove(self, provider_id: str) -> bool:
        """Remove provider configuration."""
        with self._lock:
            if provider_id in self._configs:
                del self._configs[provider_id]
                return True
            return False

    def get_all_ids(self) -> list[str]:
        """Get all provider IDs."""
        with self._lock:
            return list(self._configs.keys())

    def clear(self) -> None:
        """Clear all configurations."""
        with self._lock:
            self._configs.clear()


class EventSourcedProviderRepository(IProviderRepository):
    """
    Repository that persists providers using event sourcing.

    Features:
    - Stores events in EventStore
    - Rebuilds provider state from events
    - Supports snapshots for performance
    - Publishes events to EventBus after save
    - Caches loaded providers

    Thread-safe implementation.
    """

    def __init__(
        self,
        event_store: EventStore | None = None,
        event_bus: EventBus | None = None,
        snapshot_store: EventStoreSnapshot | None = None,
        snapshot_interval: int = 50,
    ):
        """
        Initialize the event sourced repository.

        Args:
            event_store: Event store for persistence (defaults to global)
            event_bus: Event bus for publishing (defaults to global)
            snapshot_store: Optional snapshot store for performance
            snapshot_interval: Events between snapshots
        """
        self._event_store = event_store or get_event_store()
        self._event_bus = event_bus or get_event_bus()
        self._snapshot_store = snapshot_store
        self._snapshot_interval = snapshot_interval

        # Configuration store (for non-event data like command, env)
        self._config_store = ProviderConfigStore()

        # In-memory cache for loaded providers
        self._cache: dict[str, EventSourcedProvider] = {}
        self._lock = threading.RLock()

    def add(self, provider_id: str, provider: ProviderLike) -> None:
        """
        Add or update a provider by persisting its uncommitted events.

        If provider has uncommitted events, they are appended to the event store.
        Then the events are published to the event bus.

        Args:
            provider_id: Provider identifier
            provider: Provider instance (should be EventSourcedProvider)
        """
        if not provider_id:
            raise ValueError("Provider ID cannot be empty")

        with self._lock:
            # Save configuration if it's a new provider or config changed
            self._save_config(provider_id, provider)

            # Handle non-event-sourced providers
            if not isinstance(provider, EventSourcedProvider):
                # For backward compatibility, just cache it
                self._cache[provider_id] = provider
                return

            # Get uncommitted events
            events = provider.get_uncommitted_events()

            if events:
                # Get current version from event store
                current_version = self._event_store.get_version(provider_id)

                # Append events
                new_version = self._event_store.append(
                    stream_id=provider_id,
                    events=events,
                    expected_version=current_version,
                )

                # Mark events as committed
                provider.mark_events_committed()

                # Create snapshot if needed
                if self._snapshot_store:
                    events_since_snapshot = self._get_events_since_snapshot(provider_id)
                    if events_since_snapshot >= self._snapshot_interval:
                        self._create_snapshot(provider)

                # Publish events
                for event in events:
                    self._event_bus.publish(event)

                logger.debug(
                    f"Saved {len(events)} events for provider {provider_id}, version {current_version} -> {new_version}"
                )

            # Update cache
            self._cache[provider_id] = provider

    def get(self, provider_id: str) -> ProviderLike | None:
        """
        Load a provider by rebuilding from events.

        First checks cache, then loads from event store.
        Uses snapshots if available for performance.

        Args:
            provider_id: Provider identifier

        Returns:
            Provider if found, None otherwise
        """
        with self._lock:
            # Check cache first
            if provider_id in self._cache:
                return self._cache[provider_id]

            # Load from event store
            provider = self._load_from_events(provider_id)

            if provider:
                self._cache[provider_id] = provider

            return provider

    def _load_from_events(self, provider_id: str) -> EventSourcedProvider | None:
        """Load provider from event store."""
        # Load configuration
        config = self._config_store.load(provider_id)
        if not config:
            # Check if there are events for this provider
            if not self._event_store.stream_exists(provider_id):
                return None
            # Use default config
            config = {"mode": "subprocess"}

        # Try loading from snapshot first
        snapshot = None
        snapshot_version = -1

        if self._snapshot_store:
            snapshot_data = self._snapshot_store.load_snapshot(provider_id)
            if snapshot_data:
                snapshot = ProviderSnapshot.from_dict(snapshot_data["state"])
                snapshot_version = snapshot_data["version"]

        # Load events (from snapshot version or beginning)
        events = self._event_store.load(stream_id=provider_id, from_version=snapshot_version + 1)

        # Convert stored events to domain events
        domain_events = self._hydrate_events(events)

        if snapshot:
            # Load from snapshot + subsequent events
            provider = EventSourcedProvider.from_snapshot(snapshot, domain_events)
        else:
            if not domain_events and not self._event_store.stream_exists(provider_id):
                return None

            # Load from scratch
            provider = EventSourcedProvider.from_events(
                provider_id=provider_id,
                mode=config.get("mode", "subprocess"),
                events=domain_events,
                command=config.get("command"),
                image=config.get("image"),
                endpoint=config.get("endpoint"),
                env=config.get("env"),
                idle_ttl_s=config.get("idle_ttl_s", 300),
                health_check_interval_s=config.get("health_check_interval_s", 60),
                max_consecutive_failures=config.get("max_consecutive_failures", 3),
            )

        return provider

    def _hydrate_events(self, stored_events: list[StoredEvent]) -> list[DomainEvent]:
        """Convert stored events to domain events."""
        from ..domain.events import (
            HealthCheckFailed,
            HealthCheckPassed,
            ProviderDegraded,
            ProviderIdleDetected,
            ProviderStarted,
            ProviderStateChanged,
            ProviderStopped,
            ToolInvocationCompleted,
            ToolInvocationFailed,
            ToolInvocationRequested,
        )

        event_classes = {
            "ProviderStarted": ProviderStarted,
            "ProviderStopped": ProviderStopped,
            "ProviderDegraded": ProviderDegraded,
            "ProviderStateChanged": ProviderStateChanged,
            "ToolInvocationRequested": ToolInvocationRequested,
            "ToolInvocationCompleted": ToolInvocationCompleted,
            "ToolInvocationFailed": ToolInvocationFailed,
            "HealthCheckPassed": HealthCheckPassed,
            "HealthCheckFailed": HealthCheckFailed,
            "ProviderIdleDetected": ProviderIdleDetected,
        }

        domain_events = []

        for stored in stored_events:
            event_class = event_classes.get(stored.event_type)
            if event_class:
                # Extract event data (remove event_type from data dict)
                event_data = {
                    k: v for k, v in stored.data.items() if k not in ("event_type", "event_id", "occurred_at")
                }

                try:
                    event = event_class(**event_data)
                    # Restore original event_id and occurred_at
                    event.event_id = stored.event_id
                    event.occurred_at = stored.occurred_at
                    domain_events.append(event)
                except Exception as e:
                    logger.warning(f"Failed to hydrate event {stored.event_type}: {e}")

        return domain_events

    def _save_config(self, provider_id: str, provider: ProviderLike) -> None:
        """Save provider configuration."""
        if hasattr(provider, "_command"):
            config = {
                "mode": getattr(provider, "_mode", "subprocess"),
                "command": getattr(provider, "_command", None),
                "image": getattr(provider, "_image", None),
                "endpoint": getattr(provider, "_endpoint", None),
                "env": getattr(provider, "_env", {}),
                "idle_ttl_s": getattr(provider, "_idle_ttl_s", 300),
                "health_check_interval_s": getattr(provider, "_health_check_interval_s", 60),
                "max_consecutive_failures": (
                    getattr(provider._health, "_max_consecutive_failures", 3) if hasattr(provider, "_health") else 3
                ),
            }
            self._config_store.save(provider_id, config)

    def _get_events_since_snapshot(self, provider_id: str) -> int:
        """Get number of events since last snapshot."""
        if not self._snapshot_store:
            return self._event_store.get_version(provider_id) + 1

        snapshot_data = self._snapshot_store.load_snapshot(provider_id)
        snapshot_version = snapshot_data["version"] if snapshot_data else -1

        current_version = self._event_store.get_version(provider_id)
        return current_version - snapshot_version

    def _create_snapshot(self, provider: EventSourcedProvider) -> None:
        """Create a snapshot for the provider."""
        if not self._snapshot_store:
            return

        snapshot = provider.create_snapshot()
        version = self._event_store.get_version(provider.provider_id)

        self._snapshot_store.save_snapshot(stream_id=provider.provider_id, version=version, state=snapshot.to_dict())

        logger.debug(f"Created snapshot for provider {provider.provider_id} at version {version}")

    def exists(self, provider_id: str) -> bool:
        """Check if provider exists."""
        with self._lock:
            if provider_id in self._cache:
                return True
            return self._event_store.stream_exists(provider_id) or self._config_store.load(provider_id) is not None

    def remove(self, provider_id: str) -> bool:
        """
        Remove a provider.

        Note: In event sourcing, we typically don't delete events.
        This removes from cache and config store only.
        """
        with self._lock:
            removed = False

            if provider_id in self._cache:
                del self._cache[provider_id]
                removed = True

            if self._config_store.remove(provider_id):
                removed = True

            return removed

    def get_all(self) -> dict[str, ProviderLike]:
        """Get all providers."""
        with self._lock:
            # Get all known provider IDs
            provider_ids = set(self._cache.keys())
            provider_ids.update(self._event_store.get_all_stream_ids())
            provider_ids.update(self._config_store.get_all_ids())

            result = {}
            for pid in provider_ids:
                provider = self.get(pid)
                if provider:
                    result[pid] = provider

            return result

    def get_all_ids(self) -> list[str]:
        """Get all provider IDs."""
        with self._lock:
            provider_ids = set(self._cache.keys())
            provider_ids.update(self._event_store.get_all_stream_ids())
            provider_ids.update(self._config_store.get_all_ids())
            return list(provider_ids)

    def count(self) -> int:
        """Get number of providers."""
        return len(self.get_all_ids())

    def clear(self) -> None:
        """Clear all providers from cache and config store."""
        with self._lock:
            self._cache.clear()
            self._config_store.clear()
            # Note: Event store is not cleared as events are immutable

    def invalidate_cache(self, provider_id: str | None = None) -> None:
        """Invalidate cache to force reload from event store."""
        with self._lock:
            if provider_id:
                self._cache.pop(provider_id, None)
            else:
                self._cache.clear()

    def get_event_history(self, provider_id: str) -> list[StoredEvent]:
        """
        Get full event history for a provider.

        Useful for debugging and audit.
        """
        return self._event_store.load(provider_id)

    def replay_provider(self, provider_id: str, to_version: int) -> EventSourcedProvider | None:
        """
        Replay provider to a specific version (time travel).

        Args:
            provider_id: Provider identifier
            to_version: Target version to replay to

        Returns:
            Provider at the target version, or None if not found
        """
        config = self._config_store.load(provider_id)
        if not config:
            return None

        events = self._event_store.load(provider_id, from_version=0, to_version=to_version)
        domain_events = self._hydrate_events(events)

        return EventSourcedProvider.from_events(
            provider_id=provider_id,
            mode=config.get("mode", "subprocess"),
            events=domain_events,
            command=config.get("command"),
            image=config.get("image"),
            endpoint=config.get("endpoint"),
            env=config.get("env"),
        )


# Singleton instance
_event_sourced_repository: EventSourcedProviderRepository | None = None


def get_event_sourced_repository() -> EventSourcedProviderRepository:
    """Get the global event sourced repository instance."""
    global _event_sourced_repository
    if _event_sourced_repository is None:
        _event_sourced_repository = EventSourcedProviderRepository()
    return _event_sourced_repository


def set_event_sourced_repository(repository: EventSourcedProviderRepository) -> None:
    """Set the global event sourced repository instance."""
    global _event_sourced_repository
    _event_sourced_repository = repository
