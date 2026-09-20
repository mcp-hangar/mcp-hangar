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
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar

from mcp_hangar.auth.roles import BUILTIN_ROLES
from mcp_hangar.domain.contracts.authentication import ApiKeyMetadata, IApiKeyStore
from mcp_hangar.domain.contracts.authorization import IRoleStore, validate_role_scope
from mcp_hangar.domain.contracts.event_store import ConcurrencyError, IEventStore
from mcp_hangar.domain.events import ApiKeyCreated, DomainEvent
from mcp_hangar.domain.exceptions import ExpiredCredentialsError, RevokedCredentialsError
from mcp_hangar.domain.model.event_sourced_api_key import ApiKeySnapshot, EventSourcedApiKey
from mcp_hangar.domain.model.event_sourced_role_assignment import EventSourcedRoleAssignment, RoleAssignmentSnapshot
from mcp_hangar.domain.value_objects import Principal, Role
from mcp_hangar.logging_config import get_logger

from .constant_time import constant_time_key_lookup

logger = get_logger(__name__)

_T = TypeVar("_T")

#: How many times a key revocation or rotation loads the key and decides again
#: after another writer changed it first.
_CONFLICT_ATTEMPTS = 16


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
        """Build the index by scanning all api_key streams, once."""
        if self._index is not None:
            return
        with self._lock:
            if self._index is None:
                self._scan_into_index()

    def _scan_into_index(self) -> None:
        """Scan every api_key stream and replace the index with what the log holds.

        Caller holds `self._lock`, which `_save_key` also takes to add to the
        index. The index is built into locals and published whole. It used to
        be set to an empty dict before the scan and filled in place. Another
        thread arriving mid-scan found it already "built" and missed the key it
        was looking for, and a revocation returned False as if the key did not
        exist. The principal index is published first, so
        a reader that sees `_index` set sees both.
        """
        index: dict[str, tuple[str, str]] = {}
        principal_index: dict[str, set[str]] = {}
        for stream_id in self._event_store.list_streams(f"{self.STREAM_PREFIX}:"):
            key_hash = stream_id.split(":", 1)[1]
            events = self._event_store.read_stream(stream_id)
            creation = next((event for event in events if isinstance(event, ApiKeyCreated)), None)
            if creation is not None:
                index[key_hash] = (creation.key_id, creation.principal_id)
                principal_index.setdefault(creation.principal_id, set()).add(key_hash)

        self._principal_index = principal_index
        self._index = index
        logger.info(
            "api_key_index_built",
            total_keys=len(index),
            total_principals=len(principal_index),
        )

    def _find_key(self, key_id: str) -> tuple[str, tuple[str, str]] | None:
        """Find a key's hash and index entry by its id, rescanning the log once on a miss.

        The index is built once per process, and after that it only learns
        about keys this process saved. A key created through another replica
        that shares the event store is missing from it. Revoking such a key
        here used to return False as if the key did not exist, while the key
        went on authenticating. Management calls rescan
        on a miss. Authentication does not: an unknown key arrives on that
        path, and a scan per unknown key would be a cheap way to load the
        store.
        """
        self._build_index()
        found = self._lookup_key_id(key_id)
        if found is None:
            with self._lock:
                self._scan_into_index()
            found = self._lookup_key_id(key_id)
        return found

    def _lookup_key_id(self, key_id: str) -> tuple[str, tuple[str, str]] | None:
        """The index entry for `key_id`, or None. Iterates a copy: `_save_key` may add to it meanwhile."""
        assert self._index is not None
        for key_hash, entry in list(self._index.items()):
            if entry[0] == key_id:
                return key_hash, entry
        return None

    def _stream_id(self, key_hash: str) -> str:
        """Get stream ID for a key hash."""
        return f"{self.STREAM_PREFIX}:{key_hash}"

    def _load_key(self, key_hash: str, index_entry: tuple[str, str] | None = None) -> EventSourcedApiKey | None:
        """Load API key aggregate from events.

        Args:
            key_hash: Hash of the API key to load.
            index_entry: Optional (key_id, principal_id) tuple from index.
                        If provided, skips the index lookup.
        """
        stream_id = self._stream_id(key_hash)

        # Try snapshot first
        snapshot = self._snapshot_store.get(key_hash)
        start_version = snapshot.version if snapshot else 0

        # Read events (after snapshot version if available)
        events = list(self._event_store.read_stream(stream_id, from_version=start_version))

        if not events and not snapshot:
            return None

        # Get metadata from index or provided entry
        if index_entry is not None:
            key_id, principal_id = index_entry
        else:
            # Get metadata from index or first event
            self._build_index()
            assert self._index is not None
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
                    datetime.fromtimestamp(creation_event.expires_at, tz=UTC) if creation_event.expires_at else None
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
        create_snapshot_fn: Callable,
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
            if self._index is not None and self._principal_index is not None:
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

    def _retry_on_conflict(self, operation: Callable[[], _T]) -> _T:
        """Run a load-decide-save operation again when another writer changed the key first.

        `_save_key` claims the version the key was loaded at, and the claim is
        real: whether a key may be revoked or rotated is decided on the loaded
        state. When another writer appended in between, the save raises
        `ConcurrencyError` before anything is applied. The aggregate was a local
        copy, and the index and the snapshot are touched only after a successful
        append. Running the whole operation again on a fresh load decides
        against the state that is actually there. A revocation that lost the
        race to a rotation still lands, and a rotation that lost to a revocation
        is refused as a rotation of a revoked key.

        Bounded: when every attempt loses, the caller gets the
        `ConcurrencyError`, and nothing was applied.
        """
        for _ in range(_CONFLICT_ATTEMPTS - 1):
            try:
                return operation()
            except ConcurrencyError as e:
                logger.info("api_key_write_conflict_retrying", stream_id=e.stream_id)
        return operation()

    # =========================================================================
    # IApiKeyStore Implementation
    # =========================================================================

    def get_principal_for_key(self, key_hash: str) -> Principal | None:
        """Look up principal for an API key hash.

        Uses constant-time index lookup to prevent timing attacks.
        """
        # Build index and perform constant-time lookup
        self._build_index()
        assert self._index is not None and self._principal_index is not None
        index_entry = constant_time_key_lookup(key_hash, self._index)

        if index_entry is None:
            return None

        # Load key using the index entry to avoid dict lookup
        key = self._load_key(key_hash, index_entry=index_entry)

        if key is None:
            return None

        # These three checks return at different points, which a security audit
        # flagged as a timing side-channel disclosing the key's state
        # (LLM-01). Measured before acting on it: the difference is three
        # attribute reads, ~5 ns, against a request path that has already done
        # a store load and HTTP parsing -- not observable over a network.
        #
        # More to the point, the state is disclosed OUTRIGHT: the 401 body
        # carries `e.message`, which is literally "API key has been revoked" or
        # "API key has expired". A uniform exit path would close a channel that
        # leaks nothing the response does not already say.
        #
        # If the disclosure itself is unwanted, the change to make is the
        # message, not the timing -- and that trades away a caller's ability to
        # tell "my key expired" from "my key was revoked", which is a product
        # decision rather than a hardening one.
        if key.is_revoked:
            raise RevokedCredentialsError("API key has been revoked")

        if key.is_expired:
            raise ExpiredCredentialsError("API key has expired")

        # Check rotation and grace period
        if key.is_rotated and not key.is_in_grace_period:
            raise ExpiredCredentialsError(
                message="API key has been rotated and grace period has expired",
                auth_method="api_key",
                expired_at=key.grace_until.timestamp() if key.grace_until else None,
            )

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
        created_by: str | None = None,
    ) -> str:
        """Create a new API key."""
        # Build index to check limits
        self._build_index()
        assert self._index is not None and self._principal_index is not None

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
            created_by=created_by or "system",
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
        revoked_by: str | None = None,
        reason: str | None = None,
    ) -> bool:
        """Revoke an API key.

        Raises:
            ConcurrencyError: Only when another writer changed the key on every
                attempt. Nothing was applied.
        """
        return self._retry_on_conflict(lambda: self._revoke_key_once(key_id, revoked_by, reason))

    def _revoke_key_once(self, key_id: str, revoked_by: str | None, reason: str | None) -> bool:
        """One load-decide-save pass of `revoke_key`."""
        found = self._find_key(key_id)
        if found is None:
            return False
        key_hash, _entry = found

        key = self._load_key(key_hash)
        if key is None or key.is_revoked:
            return False

        key.revoke(revoked_by=revoked_by or "system", reason=reason or "")
        self._save_key(key)

        logger.info(
            "api_key_revoked",
            key_id=key_id,
            revoked_by=revoked_by or "system",
            reason=reason or "",
        )

        return True

    def list_keys(self, principal_id: str) -> list[ApiKeyMetadata]:
        """List API keys for a principal."""
        self._build_index()
        assert self._index is not None and self._principal_index is not None

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
                        created_at=key.created_at or datetime.now(UTC),
                        expires_at=key.expires_at,
                        last_used_at=key.last_used_at,
                        revoked=key.is_revoked,
                    )
                )

        return result

    def count_keys(self, principal_id: str) -> int:
        """Count active API keys for a principal."""
        self._build_index()
        assert self._index is not None and self._principal_index is not None

        key_hashes = self._principal_index.get(principal_id, set())
        count = 0

        for key_hash in key_hashes:
            key = self._load_key(key_hash)
            if key and key.is_valid:
                count += 1

        return count

    def rotate_key(
        self,
        key_id: str,
        grace_period_seconds: float = 86400,
        rotated_by: str = "system",
    ) -> str:
        """Rotate an API key with a grace period.

        Args:
            key_id: Unique identifier of the key to rotate.
            grace_period_seconds: How long the old key remains valid (default: 24h).
            rotated_by: Principal initiating the rotation.

        Returns:
            The new raw API key (only shown once!).

        Raises:
            ValueError: If key doesn't exist, is revoked, or already rotated.
            ConcurrencyError: Only when another writer changed the key on every
                attempt. Nothing was applied, and no new key was stored.
        """
        return self._retry_on_conflict(lambda: self._rotate_key_once(key_id, grace_period_seconds, rotated_by))

    def _rotate_key_once(self, key_id: str, grace_period_seconds: float, rotated_by: str) -> str:
        """One load-decide-save pass of `rotate_key`.

        The old key is saved first, under the version it was loaded at. A
        conflict there raises before the new key is stored, so a retry leaves
        nothing behind.
        """
        found = self._find_key(key_id)
        if found is None:
            raise ValueError(f"API key not found: {key_id}")
        key_hash, index_entry = found

        # Load old key
        old_key = self._load_key(key_hash, index_entry=index_entry)
        if old_key is None:
            raise ValueError(f"API key not found: {key_id}")

        # Generate new key
        raw_key = _generate_key()
        new_key_hash = _hash_key(raw_key)
        new_key_id = secrets.token_urlsafe(8)
        grace_until = datetime.now(UTC) + timedelta(seconds=grace_period_seconds)

        # Create new key aggregate
        new_key = EventSourcedApiKey.create(
            key_hash=new_key_hash,
            key_id=new_key_id,
            principal_id=old_key.principal_id,
            name=old_key.name,
            created_by=rotated_by,
            tenant_id=old_key.tenant_id,
            groups=old_key.groups,
            expires_at=old_key.expires_at,
        )

        # Rotate old key
        old_key.rotate(new_key_id=new_key_id, grace_until=grace_until, rotated_by=rotated_by)

        # Save both aggregates
        self._save_key(old_key)
        self._save_key(new_key)

        logger.info(
            "api_key_rotated",
            old_key_id=key_id,
            new_key_id=new_key_id,
            principal_id=old_key.principal_id,
            grace_until=grace_until.isoformat(),
            rotated_by=rotated_by,
        )

        return raw_key


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
        create_snapshot_fn: Callable,
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

        # At the end of the stream, claiming no version.
        # The version used to be read here, just before the append, and
        # claimed. Read at save time, it guarded nothing: whether to record the
        # event was decided on the state loaded earlier. A writer landing
        # between the read and the append still turned the append into a
        # ConcurrencyError, and an assignment or a revocation that nothing was
        # wrong with failed. Each role event is a fact about one role in one
        # scope, and replay applies them last-writer-wins, so there is no
        # invariant across them for a version to protect.
        #
        # The snapshot below stays sound. Its version is the one this aggregate
        # was loaded at, not the one it was saved at, because recording an event
        # does not advance it. Loading from the snapshot therefore replays every
        # event appended since, including ones other writers appended in
        # between.
        new_version = self._event_store.append_at_end(stream_id, events)

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
        assigned_by: str | None = None,
    ) -> None:
        """Assign a role to a principal."""
        validate_role_scope(scope)
        # Verify role exists
        if self.get_role(role_name) is None:
            raise ValueError(f"Unknown role: {role_name}")

        assignment = self._load_assignment(principal_id)

        if assignment.assign_role(role_name, scope, assigned_by or "system"):
            self._save_assignment(assignment)
            logger.info(
                "role_assigned",
                principal_id=principal_id,
                role_name=role_name,
                scope=scope,
                assigned_by=assigned_by or "system",
            )

    def revoke_role(
        self,
        principal_id: str,
        role_name: str,
        scope: str = "global",
        revoked_by: str | None = None,
    ) -> None:
        """Revoke a role from a principal."""
        assignment = self._load_assignment(principal_id)

        if assignment.revoke_role(role_name, scope, revoked_by or "system"):
            self._save_assignment(assignment)
            logger.info(
                "role_revoked",
                principal_id=principal_id,
                role_name=role_name,
                scope=scope,
                revoked_by=revoked_by or "system",
            )

    def list_all_roles(self) -> list[Role]:
        """List all custom (non-builtin) roles."""
        with self._lock:
            return list(self._custom_roles.values())

    def delete_role(self, role_name: str) -> None:
        """Delete a custom role.

        Args:
            role_name: Name of the role to delete.

        Raises:
            RoleNotFoundError: If the role does not exist.
            CannotModifyBuiltinRoleError: If the role is a built-in role.
        """
        from mcp_hangar.domain.exceptions import CannotModifyBuiltinRoleError, RoleNotFoundError

        if role_name in BUILTIN_ROLES:
            raise CannotModifyBuiltinRoleError(role_name)
        with self._lock:
            if role_name not in self._custom_roles:
                raise RoleNotFoundError(role_name)
            del self._custom_roles[role_name]
        logger.info("custom_role_deleted", role_name=role_name)

    def update_role(
        self,
        role_name: str,
        permissions: list,
        description: str | None,
    ) -> Role:
        """Update a custom role's permissions and description.

        Args:
            role_name: Name of the role to update.
            permissions: New list of permissions.
            description: New description (None to clear).

        Returns:
            Updated Role value object.

        Raises:
            RoleNotFoundError: If the role does not exist.
            CannotModifyBuiltinRoleError: If the role is a built-in role.
        """
        from mcp_hangar.domain.exceptions import CannotModifyBuiltinRoleError, RoleNotFoundError

        if role_name in BUILTIN_ROLES:
            raise CannotModifyBuiltinRoleError(role_name)
        with self._lock:
            if role_name not in self._custom_roles:
                raise RoleNotFoundError(role_name)
            updated = Role(
                name=role_name,
                description=description or "",
                permissions=frozenset(permissions),
            )
            self._custom_roles[role_name] = updated
        logger.info("custom_role_updated", role_name=role_name)
        return updated
