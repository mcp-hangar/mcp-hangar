"""Event Sourced Repository implementation.

Stores mcp_servers by persisting their domain events and rebuilding state on load.
"""

import threading
from typing import Any

from ..domain.events import DomainEvent
from ..domain.model.event_sourced_mcp_server import EventSourcedMcpServer, McpServerSnapshot
from ..domain.repository import IMcpServerRepository, McpServerLike
from ..logging_config import get_logger
from .event_bus import EventBus, get_event_bus
from .event_store import EventStore, EventStoreSnapshot, get_event_store, StoredEvent

logger = get_logger(__name__)


class McpServerConfigStore:
    """Stores mcp_server configuration (command, image, env, etc.)"""

    def __init__(self):
        self._configs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def save(self, mcp_server_id: str, config: dict[str, Any]) -> None:
        """Save mcp_server configuration."""
        with self._lock:
            self._configs[mcp_server_id] = dict(config)

    def load(self, mcp_server_id: str) -> dict[str, Any] | None:
        """Load mcp_server configuration."""
        with self._lock:
            if mcp_server_id in self._configs:
                return dict(self._configs[mcp_server_id])
            return None

    def remove(self, mcp_server_id: str) -> bool:
        """Remove mcp_server configuration."""
        with self._lock:
            if mcp_server_id in self._configs:
                del self._configs[mcp_server_id]
                return True
            return False

    def get_all_ids(self) -> list[str]:
        """Get all mcp_server IDs."""
        with self._lock:
            return list(self._configs.keys())

    def clear(self) -> None:
        """Clear all configurations."""
        with self._lock:
            self._configs.clear()


class EventSourcedMcpServerRepository(IMcpServerRepository):
    """
    Repository that persists mcp_servers using event sourcing.

    Features:
    - Stores events in EventStore
    - Rebuilds mcp_server state from events
    - Supports snapshots for performance
    - Publishes events to EventBus after save
    - Caches loaded mcp_servers

    Supports both the old EventStore API (get_version, load, stream_exists,
    get_all_stream_ids) and the new IEventStore API (get_stream_version,
    read_stream, list_streams, save_snapshot, load_snapshot) via runtime
    method detection.

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

        # Detect API capabilities once at init
        self._has_new_api = hasattr(self._event_store, "get_stream_version")
        self._has_snapshot_methods = hasattr(self._event_store, "save_snapshot")

        # Configuration store (for non-event data like command, env)
        self._config_store = McpServerConfigStore()

        # In-memory cache for loaded mcp_servers
        self._cache: dict[str, EventSourcedMcpServer] = {}
        self._lock = threading.RLock()

    # --- API compatibility helpers ---

    def _store_get_version(self, stream_id: str) -> int:
        """Get stream version from event store, supporting both APIs."""
        if self._has_new_api:
            return self._event_store.get_stream_version(stream_id)
        return self._event_store.get_version(stream_id)

    def _store_stream_exists(self, stream_id: str) -> bool:
        """Check if stream exists, supporting both APIs."""
        if hasattr(self._event_store, "stream_exists"):
            return self._event_store.stream_exists(stream_id)
        # New API: version -1 means stream does not exist
        return self._store_get_version(stream_id) >= 0

    def _store_load_events(
        self,
        stream_id: str,
        from_version: int = 0,
        to_version: int | None = None,
    ) -> list:
        """Load events from store, supporting both APIs.

        Returns raw stored events (StoredEvent for old API, DomainEvent for new API).
        """
        if self._has_new_api:
            return self._event_store.read_stream(stream_id, from_version=from_version)
        return self._event_store.load(stream_id, from_version=from_version, to_version=to_version)

    def _store_get_all_stream_ids(self) -> list[str]:
        """Get all stream IDs, supporting both APIs."""
        if hasattr(self._event_store, "get_all_stream_ids"):
            return self._event_store.get_all_stream_ids()
        return self._event_store.list_streams()

    def add(self, mcp_server_id: str, mcp_server: McpServerLike) -> None:
        """
        Add or update a mcp_server by persisting its uncommitted events.

        If mcp_server has uncommitted events, they are appended to the event store.
        Then the events are published to the event bus.

        Args:
            mcp_server_id: McpServer identifier
            mcp_server: McpServer instance (should be EventSourcedMcpServer)
        """
        if not mcp_server_id:
            raise ValueError("McpServer ID cannot be empty")

        with self._lock:
            # Save configuration if it's a new mcp_server or config changed
            self._save_config(mcp_server_id, mcp_server)

            # Handle non-event-sourced mcp_servers
            if not isinstance(mcp_server, EventSourcedMcpServer):
                # For backward compatibility, just cache it
                self._cache[mcp_server_id] = mcp_server
                return

            # Get uncommitted events
            events = mcp_server.get_uncommitted_events()

            if events:
                # Get current version from event store
                current_version = self._store_get_version(mcp_server_id)

                # Append events
                new_version = self._event_store.append(
                    stream_id=mcp_server_id,
                    events=events,
                    expected_version=current_version,
                )

                # Mark events as committed
                mcp_server.mark_events_committed()

                # Create snapshot if needed
                has_snapshot_support = self._has_snapshot_methods or self._snapshot_store
                if has_snapshot_support:
                    events_since_snapshot = self._get_events_since_snapshot(mcp_server_id)
                    if events_since_snapshot >= self._snapshot_interval:
                        self._create_snapshot(mcp_server)

                # Publish events
                for event in events:
                    self._event_bus.publish(event)

                logger.debug(
                    "Saved %d events for mcp_server %s, version %s -> %s",
                    len(events),
                    mcp_server_id,
                    current_version,
                    new_version,
                )

            # Update cache
            self._cache[mcp_server_id] = mcp_server

    def get(self, mcp_server_id: str) -> McpServerLike | None:
        """
        Load a mcp_server by rebuilding from events.

        First checks cache, then loads from event store.
        Uses snapshots if available for performance.

        Args:
            mcp_server_id: McpServer identifier

        Returns:
            McpServer if found, None otherwise
        """
        with self._lock:
            # Check cache first
            if mcp_server_id in self._cache:
                return self._cache[mcp_server_id]

            # Load from event store
            mcp_server = self._load_from_events(mcp_server_id)

            if mcp_server:
                self._cache[mcp_server_id] = mcp_server

            return mcp_server

    def _load_from_events(self, mcp_server_id: str) -> EventSourcedMcpServer | None:
        """Load mcp_server from event store."""
        # Load configuration
        config = self._config_store.load(mcp_server_id)
        if not config:
            # Check if there are events for this mcp_server
            if not self._store_stream_exists(mcp_server_id):
                return None
            # Use default config
            config = {"mode": "subprocess"}

        # Try loading from snapshot first
        snapshot = None
        snapshot_version = -1

        if self._has_snapshot_methods:
            # New IEventStore with built-in snapshot support
            snapshot_data = self._event_store.load_snapshot(mcp_server_id)
            if snapshot_data:
                snapshot = McpServerSnapshot.from_dict(snapshot_data["state"])
                snapshot_version = snapshot_data["version"]
        elif self._snapshot_store:
            # Legacy snapshot store
            snapshot_data = self._snapshot_store.load_snapshot(mcp_server_id)
            if snapshot_data:
                snapshot = McpServerSnapshot.from_dict(snapshot_data["state"])
                snapshot_version = snapshot_data["version"]

        # Load events (from snapshot version or beginning)
        raw_events = self._store_load_events(stream_id=mcp_server_id, from_version=snapshot_version + 1)

        # Convert to domain events (new API returns DomainEvent directly, old returns StoredEvent)
        if self._has_new_api:
            domain_events = raw_events
        else:
            domain_events = self._hydrate_events(raw_events)

        if snapshot:
            # Load from snapshot + subsequent events
            mcp_server = EventSourcedMcpServer.from_snapshot(snapshot, domain_events)
        else:
            if not domain_events and not self._store_stream_exists(mcp_server_id):
                return None

            # Load from scratch
            mcp_server = EventSourcedMcpServer.from_events(
                mcp_server_id=mcp_server_id,
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

        return mcp_server

    def _hydrate_events(self, stored_events: list[StoredEvent]) -> list[DomainEvent]:
        """Convert stored events to domain events."""
        from ..domain.events import (
            HealthCheckFailed,
            HealthCheckPassed,
            McpServerDegraded,
            McpServerIdleDetected,
            McpServerStarted,
            McpServerStateChanged,
            McpServerStopped,
            ToolInvocationCompleted,
            ToolInvocationFailed,
            ToolInvocationRequested,
        )

        event_classes = {
            "McpServerStarted": McpServerStarted,
            "McpServerStopped": McpServerStopped,
            "McpServerDegraded": McpServerDegraded,
            "McpServerStateChanged": McpServerStateChanged,
            "ToolInvocationRequested": ToolInvocationRequested,
            "ToolInvocationCompleted": ToolInvocationCompleted,
            "ToolInvocationFailed": ToolInvocationFailed,
            "HealthCheckPassed": HealthCheckPassed,
            "HealthCheckFailed": HealthCheckFailed,
            "McpServerIdleDetected": McpServerIdleDetected,
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
                except Exception as e:  # noqa: BLE001 -- infra-boundary: skip malformed event during replay
                    logger.warning(f"Failed to hydrate event {stored.event_type}: {e}")

        return domain_events

    def _save_config(self, mcp_server_id: str, mcp_server: McpServerLike) -> None:
        """Save mcp_server configuration."""
        if hasattr(mcp_server, "_command"):
            config = {
                "mode": getattr(mcp_server, "_mode", "subprocess"),
                "command": getattr(mcp_server, "_command", None),
                "image": getattr(mcp_server, "_image", None),
                "endpoint": getattr(mcp_server, "_endpoint", None),
                "env": getattr(mcp_server, "_env", {}),
                "idle_ttl_s": getattr(mcp_server, "_idle_ttl_s", 300),
                "health_check_interval_s": getattr(mcp_server, "_health_check_interval_s", 60),
                "max_consecutive_failures": (
                    getattr(mcp_server._health, "_max_consecutive_failures", 3) if hasattr(mcp_server, "_health") else 3
                ),
            }
            self._config_store.save(mcp_server_id, config)

    def _get_events_since_snapshot(self, mcp_server_id: str) -> int:
        """Get number of events since last snapshot."""
        snapshot_version = -1

        if self._has_snapshot_methods:
            snapshot_data = self._event_store.load_snapshot(mcp_server_id)
            snapshot_version = snapshot_data["version"] if snapshot_data else -1
        elif self._snapshot_store:
            snapshot_data = self._snapshot_store.load_snapshot(mcp_server_id)
            snapshot_version = snapshot_data["version"] if snapshot_data else -1

        current_version = self._store_get_version(mcp_server_id)
        return current_version - snapshot_version

    def _create_snapshot(self, mcp_server: EventSourcedMcpServer) -> None:
        """Create a snapshot for the mcp_server."""
        snapshot = mcp_server.create_snapshot()
        version = self._store_get_version(mcp_server.mcp_server_id)

        if self._has_snapshot_methods:
            self._event_store.save_snapshot(
                stream_id=mcp_server.mcp_server_id, version=version, state=snapshot.to_dict()
            )
        elif self._snapshot_store:
            self._snapshot_store.save_snapshot(
                stream_id=mcp_server.mcp_server_id, version=version, state=snapshot.to_dict()
            )
        else:
            return

        logger.debug(f"Created snapshot for mcp_server {mcp_server.mcp_server_id} at version {version}")

    def exists(self, mcp_server_id: str) -> bool:
        """Check if mcp_server exists."""
        with self._lock:
            if mcp_server_id in self._cache:
                return True
            return self._store_stream_exists(mcp_server_id) or self._config_store.load(mcp_server_id) is not None

    def remove(self, mcp_server_id: str) -> bool:
        """
        Remove a mcp_server.

        Note: In event sourcing, we typically don't delete events.
        This removes from cache and config store only.
        """
        with self._lock:
            removed = False

            if mcp_server_id in self._cache:
                del self._cache[mcp_server_id]
                removed = True

            if self._config_store.remove(mcp_server_id):
                removed = True

            return removed

    def get_all(self) -> dict[str, McpServerLike]:
        """Get all mcp_servers."""
        with self._lock:
            # Get all known mcp_server IDs
            mcp_server_ids = set(self._cache.keys())
            mcp_server_ids.update(self._store_get_all_stream_ids())
            mcp_server_ids.update(self._config_store.get_all_ids())

            result = {}
            for pid in mcp_server_ids:
                mcp_server = self.get(pid)
                if mcp_server:
                    result[pid] = mcp_server

            return result

    def get_all_ids(self) -> list[str]:
        """Get all mcp_server IDs."""
        with self._lock:
            mcp_server_ids = set(self._cache.keys())
            mcp_server_ids.update(self._store_get_all_stream_ids())
            mcp_server_ids.update(self._config_store.get_all_ids())
            return list(mcp_server_ids)

    def count(self) -> int:
        """Get number of mcp_servers."""
        return len(self.get_all_ids())

    def clear(self) -> None:
        """Clear all mcp_servers from cache and config store."""
        with self._lock:
            self._cache.clear()
            self._config_store.clear()
            # Note: Event store is not cleared as events are immutable

    def invalidate_cache(self, mcp_server_id: str | None = None) -> None:
        """Invalidate cache to force reload from event store."""
        with self._lock:
            if mcp_server_id:
                self._cache.pop(mcp_server_id, None)
            else:
                self._cache.clear()

    def get_event_history(self, mcp_server_id: str) -> list:
        """
        Get full event history for a mcp_server.

        Useful for debugging and audit.
        Returns StoredEvent list (old API) or DomainEvent list (new API).
        """
        return self._store_load_events(mcp_server_id)

    def replay_mcp_server(self, mcp_server_id: str, to_version: int) -> EventSourcedMcpServer | None:
        """
        Replay mcp_server to a specific version (time travel).

        Args:
            mcp_server_id: McpServer identifier
            to_version: Target version to replay to

        Returns:
            McpServer at the target version, or None if not found
        """
        config = self._config_store.load(mcp_server_id)
        if not config:
            return None

        raw_events = self._store_load_events(mcp_server_id, from_version=0, to_version=to_version)

        if self._has_new_api:
            domain_events = raw_events
        else:
            domain_events = self._hydrate_events(raw_events)

        return EventSourcedMcpServer.from_events(
            mcp_server_id=mcp_server_id,
            mode=config.get("mode", "subprocess"),
            events=domain_events,
            command=config.get("command"),
            image=config.get("image"),
            endpoint=config.get("endpoint"),
            env=config.get("env"),
        )


# Singleton instance
_event_sourced_repository: EventSourcedMcpServerRepository | None = None


def get_event_sourced_repository() -> EventSourcedMcpServerRepository:
    """Get the global event sourced repository instance."""
    global _event_sourced_repository
    if _event_sourced_repository is None:
        _event_sourced_repository = EventSourcedMcpServerRepository()
    return _event_sourced_repository


def set_event_sourced_repository(repository: EventSourcedMcpServerRepository) -> None:
    """Set the global event sourced repository instance."""
    global _event_sourced_repository
    _event_sourced_repository = repository

EventSourcedProviderRepository = EventSourcedMcpServerRepository
