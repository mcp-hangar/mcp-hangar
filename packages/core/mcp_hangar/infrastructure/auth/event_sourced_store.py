"""Event Sourced Repository for Auth aggregates.

Stores API Keys and Role Assignments by persisting their domain events
and rebuilding state on load. Provides:
- Event persistence via IEventStore
- Snapshot support for performance
- Event publishing via EventBus
"""

import hashlib
import secrets
import threading
from datetime import UTC, datetime
from typing import Protocol

from ...domain.contracts.authentication import ApiKeyMetadata, IApiKeyStore
from ...domain.contracts.authorization import IRoleStore
from ...domain.contracts.event_store import IEventStore
from ...domain.events import ApiKeyCreated, DomainEvent
from ...domain.exceptions import ExpiredCredentialsError, RevokedCredentialsError
from ...domain.model.event_sourced_api_key import ApiKeySnapshot, EventSourcedApiKey
from ...domain.model.event_sourced_role_assignment import EventSourcedRoleAssignment, RoleAssignmentSnapshot
from ...domain.security.roles import BUILTIN_ROLES
from ...domain.value_objects import Principal, Role
from ...logging_config import get_logger

logger = get_logger(__name__)


class IEventPublisher(Protocol):
    """Interface for event publishing (Dependency Inversion)."""

    def publish(self, event: DomainEvent) -> None:
        """Publish a domain event."""
        ...


def _generate_key() -> str:
    """Generate a new API key."""
    return f"mcp_{secrets.token_urlsafe(32)}"


def _hash_key(key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(key.encode()).hexdigest()


class EventSourcedApiKeyStore(IApiKeyStore):
    """Event Sourced API Key Store.

    Persists API keys as event streams and rebuilds state on load.

    Stream naming: "api_key:{key_hash}"

    Features:
    - Full audit trail via events
    - Snapshot support for large streams
    - Event publishing for integrations
    """

    STREAM_PREFIX = "api_key"
    SNAPSHOT_INTERVAL = 50  # Events between snapshots
    MAX_KEYS_PER_PRINCIPAL = 100

    def __init__(
        self,
        event_store: IEventStore,
        event_publisher: IEventPublisher | None = None,
        snapshot_store: dict[str, ApiKeySnapshot] | None = None,
    ):
        """Initialize the event sourced store.

        Args:
            event_store: Event store for persistence.
            event_publisher: Optional publisher for events (e.g., EventBus).
            snapshot_store: Optional snapshot cache.
        """
        self._event_store = event_store
        self._event_publisher = event_publisher
        self._snapshot_store = snapshot_store or {}
        self._lock = threading.RLock()

        # Index: key_hash -> (key_id, principal_id)
        # Built by scanning events on first access
        self._index: dict[str, tuple[str, str]] | None = None
        # Reverse index: principal_id -> set of key_hashes
        self._principal_index: dict[str, set[str]] | None = None

    def _build_index(self) -> None:
        """Build index by scanning all api_key streams."""
        if self._index is not None:
            return

        self._index = {}
        self._principal_index = {}

        # Scan all streams with our prefix
        # This is expensive but only done once
        for stream_id in self._event_store.list_streams(f"{self.STREAM_PREFIX}:"):
            key_hash = stream_id.split(":", 1)[1]
            events = list(self._event_store.read_stream(stream_id))

            if events:
                # Find creation event for metadata
                for event in events:
                    if isinstance(event, ApiKeyCreated):
                        self._index[key_hash] = (event.key_id, event.principal_id)

                        if event.principal_id not in self._principal_index:
                            self._principal_index[event.principal_id] = set()
                        self._principal_index[event.principal_id].add(key_hash)
                        break

        logger.info(
            "api_key_index_built",
            total_keys=len(self._index),
            total_principals=len(self._principal_index),
        )

    def _stream_id(self, key_hash: str) -> str:
        """Get stream ID for a key hash."""
        return f"{self.STREAM_PREFIX}:{key_hash}"

    def _load_key(self, key_hash: str) -> EventSourcedApiKey | None:
        """Load API key aggregate from events."""
        stream_id = self._stream_id(key_hash)

        # Try snapshot first
        snapshot = self._snapshot_store.get(key_hash)
        start_version = snapshot.version if snapshot else 0

        # Read events (after snapshot version if available)
        events = list(self._event_store.read_stream(stream_id, from_version=start_version))

        if not events and not snapshot:
            return None

        # Get metadata from index or first event
        self._build_index()
        if key_hash not in self._index:
            return None

        key_id, principal_id = self._index[key_hash]

        if snapshot:
            key = EventSourcedApiKey.from_snapshot(snapshot, events)
        else:
            # Need to find creation event for full metadata
            all_events = list(self._event_store.read_stream(stream_id))
            creation_event = next((e for e in all_events if isinstance(e, ApiKeyCreated)), None)

            if not creation_event:
                return None

            key = EventSourcedApiKey.from_events(
                key_hash=key_hash,
                key_id=key_id,
                principal_id=principal_id,
                name=creation_event.key_name,
                events=all_events,
                expires_at=(
                    datetime.fromtimestamp(creation_event.expires_at, tz=UTC)
                    if creation_event.expires_at
                    else None
                ),
            )

        return key

    def _publish_events(self, events: list[DomainEvent]) -> None:
        """Publish events if publisher is configured."""
        if self._event_publisher:
            for event in events:
                self._event_publisher.publish(event)

    def _maybe_create_snapshot(
        self,
        key_id: str,
        new_version: int,
        create_snapshot_fn: callable,
    ) -> None:
        """Create snapshot if threshold reached."""
        if new_version < self.SNAPSHOT_INTERVAL:
            return

        existing = self._snapshot_store.get(key_id)
        existing_version = existing.version if existing else 0
        events_since = new_version - existing_version

        if events_since >= self.SNAPSHOT_INTERVAL:
            self._snapshot_store[key_id] = create_snapshot_fn()

    def _save_key(self, key: EventSourcedApiKey) -> None:
        """Save API key events and publish."""
        events = key.collect_events()
        if not events:
            return

        stream_id = self._stream_id(key.key_hash)

        # Append events
        new_version = self._event_store.append(
            stream_id=stream_id,
            events=events,
            expected_version=key.version - len(events),
        )

        # Update index
        with self._lock:
            if self._index is not None:
                self._index[key.key_hash] = (key.key_id, key.principal_id)
                if key.principal_id not in self._principal_index:
                    self._principal_index[key.principal_id] = set()
                self._principal_index[key.principal_id].add(key.key_hash)

        # Create snapshot if needed
        self._maybe_create_snapshot(key.key_hash, new_version, key.create_snapshot)

        # Publish events
        self._publish_events(events)

        logger.debug(
            "api_key_events_saved",
            key_id=key.key_id,
            events_count=len(events),
            new_version=new_version,
        )

    # =========================================================================
    # IApiKeyStore Implementation
    # =========================================================================

    def get_principal_for_key(self, key_hash: str) -> Principal | None:
        """Look up principal for an API key hash."""
        key = self._load_key(key_hash)

        if key is None:
            return None

        if key.is_revoked:
            raise RevokedCredentialsError("API key has been revoked")

        if key.is_expired:
            raise ExpiredCredentialsError("API key has expired")

        # Record usage
        key.record_usage()

        return key.to_principal()

    def create_key(
        self,
        principal_id: str,
        name: str,
        expires_at: datetime | None = None,
        groups: frozenset[str] | None = None,
        tenant_id: str | None = None,
        created_by: str = "system",
    ) -> str:
        """Create a new API key."""
        # Build index to check limits
        self._build_index()

        # Check key limit
        existing_keys = self._principal_index.get(principal_id, set())
        if len(existing_keys) >= self.MAX_KEYS_PER_PRINCIPAL:
            raise ValueError(f"Principal {principal_id} has reached maximum API keys ({self.MAX_KEYS_PER_PRINCIPAL})")

        # Generate key
        raw_key = _generate_key()
        key_hash = _hash_key(raw_key)
        key_id = secrets.token_urlsafe(8)

        # Create aggregate
        key = EventSourcedApiKey.create(
            key_hash=key_hash,
            key_id=key_id,
            principal_id=principal_id,
            name=name,
            created_by=created_by,
            tenant_id=tenant_id,
            groups=groups,
            expires_at=expires_at,
        )

        # Save
        self._save_key(key)

        logger.info(
            "api_key_created",
            key_id=key_id,
            principal_id=principal_id,
            name=name,
        )

        return raw_key

    def revoke_key(
        self,
        key_id: str,
        revoked_by: str = "system",
        reason: str = "",
    ) -> bool:
        """Revoke an API key."""
        # Find key by key_id
        self._build_index()

        key_hash = None
        for kh, (kid, _) in self._index.items():
            if kid == key_id:
                key_hash = kh
                break

        if key_hash is None:
            return False

        key = self._load_key(key_hash)
        if key is None or key.is_revoked:
            return False

        key.revoke(revoked_by=revoked_by, reason=reason)
        self._save_key(key)

        logger.info(
            "api_key_revoked",
            key_id=key_id,
            revoked_by=revoked_by,
            reason=reason,
        )

        return True

    def list_keys(self, principal_id: str) -> list[ApiKeyMetadata]:
        """List API keys for a principal."""
        self._build_index()

        key_hashes = self._principal_index.get(principal_id, set())
        result = []

        for key_hash in key_hashes:
            key = self._load_key(key_hash)
            if key:
                result.append(
                    ApiKeyMetadata(
                        key_id=key.key_id,
                        name=key.name,
                        principal_id=key.principal_id,
                        created_at=key.created_at,
                        expires_at=key.expires_at,
                        last_used_at=key.last_used_at,
                        revoked=key.is_revoked,
                    )
                )

        return result

    def count_keys(self, principal_id: str) -> int:
        """Count active API keys for a principal."""
        self._build_index()

        key_hashes = self._principal_index.get(principal_id, set())
        count = 0

        for key_hash in key_hashes:
            key = self._load_key(key_hash)
            if key and key.is_valid:
                count += 1

        return count


class EventSourcedRoleStore(IRoleStore):
    """Event Sourced Role Store.

    Persists role assignments as event streams and rebuilds state on load.

    Stream naming: "role_assignment:{principal_id}"

    Features:
    - Full audit trail via events
    - Snapshot support for large streams
    - Event publishing for integrations
    """

    STREAM_PREFIX = "role_assignment"
    SNAPSHOT_INTERVAL = 50

    def __init__(
        self,
        event_store: IEventStore,
        event_publisher: IEventPublisher | None = None,
        snapshot_store: dict[str, RoleAssignmentSnapshot] | None = None,
    ):
        """Initialize the event sourced store.

        Args:
            event_store: Event store for persistence.
            event_publisher: Optional publisher for events (e.g., EventBus).
            snapshot_store: Optional snapshot cache.
        """
        self._event_store = event_store
        self._event_publisher = event_publisher
        self._snapshot_store = snapshot_store or {}
        self._lock = threading.RLock()

        # Custom roles (in addition to built-in)
        self._custom_roles: dict[str, Role] = {}

    def _stream_id(self, principal_id: str) -> str:
        """Get stream ID for a principal."""
        return f"{self.STREAM_PREFIX}:{principal_id}"

    def _load_assignment(self, principal_id: str) -> EventSourcedRoleAssignment:
        """Load role assignment aggregate from events."""
        stream_id = self._stream_id(principal_id)

        # Try snapshot first
        snapshot = self._snapshot_store.get(principal_id)
        start_version = snapshot.version if snapshot else 0

        # Read events
        events = list(self._event_store.read_stream(stream_id, from_version=start_version))

        if snapshot:
            return EventSourcedRoleAssignment.from_snapshot(snapshot, events)
        elif events:
            return EventSourcedRoleAssignment.from_events(principal_id, events)
        else:
            return EventSourcedRoleAssignment(principal_id)

    def _publish_events(self, events: list[DomainEvent]) -> None:
        """Publish events if publisher is configured."""
        if self._event_publisher:
            for event in events:
                self._event_publisher.publish(event)

    def _maybe_create_snapshot(
        self,
        key_id: str,
        new_version: int,
        create_snapshot_fn: callable,
    ) -> None:
        """Create snapshot if threshold reached."""
        if new_version < self.SNAPSHOT_INTERVAL:
            return

        existing = self._snapshot_store.get(key_id)
        existing_version = existing.version if existing else 0
        events_since = new_version - existing_version

        if events_since >= self.SNAPSHOT_INTERVAL:
            self._snapshot_store[key_id] = create_snapshot_fn()

    def _save_assignment(self, assignment: EventSourcedRoleAssignment) -> None:
        """Save role assignment events and publish."""
        events = assignment.collect_events()
        if not events:
            return

        stream_id = self._stream_id(assignment.principal_id)
        current_version = self._event_store.get_stream_version(stream_id)

        # Append events
        new_version = self._event_store.append(
            stream_id=stream_id,
            events=events,
            expected_version=current_version,
        )

        # Create snapshot if needed
        self._maybe_create_snapshot(
            assignment.principal_id,
            new_version,
            assignment.create_snapshot,
        )

        # Publish events
        self._publish_events(events)

        logger.debug(
            "role_assignment_events_saved",
            principal_id=assignment.principal_id,
            events_count=len(events),
            new_version=new_version,
        )

    # =========================================================================
    # IRoleStore Implementation
    # =========================================================================

    def get_role(self, role_name: str) -> Role | None:
        """Get role by name."""
        # Check built-in first
        if role_name in BUILTIN_ROLES:
            return BUILTIN_ROLES[role_name]

        # Check custom roles
        return self._custom_roles.get(role_name)

    def add_role(self, role: Role) -> None:
        """Add a custom role."""
        if role.name in BUILTIN_ROLES:
            raise ValueError(f"Cannot override built-in role: {role.name}")

        self._custom_roles[role.name] = role
        logger.info("custom_role_added", role_name=role.name)

    def get_roles_for_principal(
        self,
        principal_id: str,
        scope: str = "*",
    ) -> list[Role]:
        """Get all roles assigned to a principal."""
        assignment = self._load_assignment(principal_id)
        role_names = assignment.get_role_names(scope)

        roles = []
        for name in role_names:
            role = self.get_role(name)
            if role:
                roles.append(role)

        return roles

    def assign_role(
        self,
        principal_id: str,
        role_name: str,
        scope: str = "global",
        assigned_by: str = "system",
    ) -> None:
        """Assign a role to a principal."""
        # Verify role exists
        if self.get_role(role_name) is None:
            raise ValueError(f"Unknown role: {role_name}")

        assignment = self._load_assignment(principal_id)

        if assignment.assign_role(role_name, scope, assigned_by):
            self._save_assignment(assignment)
            logger.info(
                "role_assigned",
                principal_id=principal_id,
                role_name=role_name,
                scope=scope,
                assigned_by=assigned_by,
            )

    def revoke_role(
        self,
        principal_id: str,
        role_name: str,
        scope: str = "global",
        revoked_by: str = "system",
    ) -> None:
        """Revoke a role from a principal."""
        assignment = self._load_assignment(principal_id)

        if assignment.revoke_role(role_name, scope, revoked_by):
            self._save_assignment(assignment)
            logger.info(
                "role_revoked",
                principal_id=principal_id,
                role_name=role_name,
                scope=scope,
                revoked_by=revoked_by,
            )
