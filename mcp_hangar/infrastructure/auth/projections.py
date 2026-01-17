"""Auth Projections - Read models built from events.

Projections listen to domain events and build optimized read models
for queries. They are part of CQRS read side.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import threading
from typing import Any

from ...domain.contracts.event_store import IEventStore
from ...domain.events import ApiKeyCreated, ApiKeyRevoked, DomainEvent, RoleAssigned, RoleRevoked
from ...logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ApiKeyReadModel:
    """Read model for API key queries."""

    key_id: str
    key_hash: str
    principal_id: str
    name: str
    tenant_id: str | None
    groups: list[str]
    created_at: datetime
    created_by: str
    expires_at: datetime | None
    revoked: bool
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revocation_reason: str | None = None


@dataclass
class RoleAssignmentReadModel:
    """Read model for role assignment queries."""

    principal_id: str
    role_name: str
    scope: str
    assigned_at: datetime
    assigned_by: str


class AuthProjection:
    """Projection that builds auth read models from events.

    Processes events from the event store and maintains in-memory
    read models optimized for queries.

    Features:
    - Indexes by key_hash, key_id, and principal_id
    - Role assignments indexed by principal_id
    - Thread-safe access
    - Supports catchup from event store
    """

    def __init__(self, event_store: IEventStore | None = None):
        """Initialize the projection.

        Args:
            event_store: Optional event store for catchup.
        """
        self._event_store = event_store
        self._lock = threading.RLock()

        # API Key indexes
        self._keys_by_hash: dict[str, ApiKeyReadModel] = {}
        self._keys_by_id: dict[str, ApiKeyReadModel] = {}
        self._keys_by_principal: dict[str, list[str]] = {}  # principal -> key_ids

        # Role assignment indexes
        self._roles_by_principal: dict[str, list[RoleAssignmentReadModel]] = {}

        # Track position for incremental updates
        self._last_position = 0

    def catchup(self) -> int:
        """Catch up with events from event store.

        Reads all events from last known position and applies them.

        Returns:
            Number of events processed.
        """
        if not self._event_store:
            return 0

        count = 0
        for position, stream_id, event in self._event_store.read_all(from_position=self._last_position):
            self.apply(event)
            self._last_position = position
            count += 1

        logger.info(
            "auth_projection_catchup_complete",
            events_processed=count,
            last_position=self._last_position,
        )

        return count

    def apply(self, event: DomainEvent) -> None:
        """Apply a domain event to update read models.

        Args:
            event: Event to apply.
        """
        if isinstance(event, ApiKeyCreated):
            self._apply_key_created(event)
        elif isinstance(event, ApiKeyRevoked):
            self._apply_key_revoked(event)
        elif isinstance(event, RoleAssigned):
            self._apply_role_assigned(event)
        elif isinstance(event, RoleRevoked):
            self._apply_role_revoked(event)

    def _apply_key_created(self, event: ApiKeyCreated) -> None:
        """Apply ApiKeyCreated event."""
        with self._lock:
            # We don't have key_hash in the event - that's stored in stream_id
            # For now, create read model without hash (can be updated later)
            model = ApiKeyReadModel(
                key_id=event.key_id,
                key_hash="",  # Will be set when we know it
                principal_id=event.principal_id,
                name=event.key_name,
                tenant_id=None,  # Not in event
                groups=[],  # Not in event
                created_at=datetime.fromtimestamp(event.occurred_at, tz=timezone.utc),
                created_by=event.created_by,
                expires_at=datetime.fromtimestamp(event.expires_at, tz=timezone.utc) if event.expires_at else None,
                revoked=False,
            )

            self._keys_by_id[event.key_id] = model

            if event.principal_id not in self._keys_by_principal:
                self._keys_by_principal[event.principal_id] = []
            self._keys_by_principal[event.principal_id].append(event.key_id)

    def _apply_key_revoked(self, event: ApiKeyRevoked) -> None:
        """Apply ApiKeyRevoked event."""
        with self._lock:
            if event.key_id in self._keys_by_id:
                model = self._keys_by_id[event.key_id]
                # Create updated model using immutable pattern
                self._keys_by_id[event.key_id] = replace(
                    model,
                    revoked=True,
                    revoked_at=datetime.fromtimestamp(event.occurred_at, tz=timezone.utc),
                    revoked_by=event.revoked_by,
                    revocation_reason=event.reason,
                )

    def _apply_role_assigned(self, event: RoleAssigned) -> None:
        """Apply RoleAssigned event."""
        with self._lock:
            model = RoleAssignmentReadModel(
                principal_id=event.principal_id,
                role_name=event.role_name,
                scope=event.scope,
                assigned_at=datetime.fromtimestamp(event.occurred_at, tz=timezone.utc),
                assigned_by=event.assigned_by,
            )

            if event.principal_id not in self._roles_by_principal:
                self._roles_by_principal[event.principal_id] = []

            # Check if already assigned (idempotency)
            existing = next(
                (
                    r
                    for r in self._roles_by_principal[event.principal_id]
                    if r.role_name == event.role_name and r.scope == event.scope
                ),
                None,
            )
            if not existing:
                self._roles_by_principal[event.principal_id].append(model)

    def _apply_role_revoked(self, event: RoleRevoked) -> None:
        """Apply RoleRevoked event."""
        with self._lock:
            if event.principal_id in self._roles_by_principal:
                self._roles_by_principal[event.principal_id] = [
                    r
                    for r in self._roles_by_principal[event.principal_id]
                    if not (r.role_name == event.role_name and r.scope == event.scope)
                ]

    # =========================================================================
    # Queries
    # =========================================================================

    def get_key_by_id(self, key_id: str) -> ApiKeyReadModel | None:
        """Get API key by ID."""
        with self._lock:
            return self._keys_by_id.get(key_id)

    def get_keys_for_principal(self, principal_id: str) -> list[ApiKeyReadModel]:
        """Get all API keys for a principal."""
        with self._lock:
            key_ids = self._keys_by_principal.get(principal_id, [])
            return [self._keys_by_id[kid] for kid in key_ids if kid in self._keys_by_id]

    def get_active_key_count(self, principal_id: str) -> int:
        """Get count of active (non-revoked) keys for a principal."""
        keys = self.get_keys_for_principal(principal_id)
        return sum(1 for k in keys if not k.revoked)

    def get_roles_for_principal(self, principal_id: str) -> list[RoleAssignmentReadModel]:
        """Get all role assignments for a principal."""
        with self._lock:
            return list(self._roles_by_principal.get(principal_id, []))

    def has_role(self, principal_id: str, role_name: str, scope: str = "*") -> bool:
        """Check if principal has a specific role."""
        roles = self.get_roles_for_principal(principal_id)
        for role in roles:
            if role.role_name == role_name:
                if scope == "*" or role.scope == scope or role.scope == "global":
                    return True
        return False

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> dict[str, Any]:
        """Get projection statistics."""
        with self._lock:
            total_keys = len(self._keys_by_id)
            revoked_keys = sum(1 for k in self._keys_by_id.values() if k.revoked)

            total_assignments = sum(len(roles) for roles in self._roles_by_principal.values())

            return {
                "total_api_keys": total_keys,
                "active_api_keys": total_keys - revoked_keys,
                "revoked_api_keys": revoked_keys,
                "total_principals_with_keys": len(self._keys_by_principal),
                "total_role_assignments": total_assignments,
                "total_principals_with_roles": len(self._roles_by_principal),
                "last_event_position": self._last_position,
            }


class AuthAuditLog:
    """Audit log projection for auth events.

    Maintains a time-ordered log of all auth events for audit purposes.
    """

    def __init__(self, max_entries: int = 10000):
        """Initialize audit log.

        Args:
            max_entries: Maximum entries to keep in memory.
        """
        self._max_entries = max_entries
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def apply(self, event: DomainEvent) -> None:
        """Apply event to audit log."""
        entry = self._event_to_entry(event)
        if entry:
            with self._lock:
                self._entries.append(entry)

                # Trim if over limit
                if len(self._entries) > self._max_entries:
                    self._entries = self._entries[-self._max_entries :]

    def _event_to_entry(self, event: DomainEvent) -> dict[str, Any] | None:
        """Convert event to audit entry."""
        if isinstance(event, ApiKeyCreated):
            return {
                "timestamp": event.occurred_at,
                "event_type": "api_key_created",
                "principal_id": event.principal_id,
                "details": {
                    "key_id": event.key_id,
                    "key_name": event.key_name,
                    "created_by": event.created_by,
                    "expires_at": event.expires_at,
                },
            }

        elif isinstance(event, ApiKeyRevoked):
            return {
                "timestamp": event.occurred_at,
                "event_type": "api_key_revoked",
                "principal_id": event.principal_id,
                "details": {
                    "key_id": event.key_id,
                    "revoked_by": event.revoked_by,
                    "reason": event.reason,
                },
            }

        elif isinstance(event, RoleAssigned):
            return {
                "timestamp": event.occurred_at,
                "event_type": "role_assigned",
                "principal_id": event.principal_id,
                "details": {
                    "role_name": event.role_name,
                    "scope": event.scope,
                    "assigned_by": event.assigned_by,
                },
            }

        elif isinstance(event, RoleRevoked):
            return {
                "timestamp": event.occurred_at,
                "event_type": "role_revoked",
                "principal_id": event.principal_id,
                "details": {
                    "role_name": event.role_name,
                    "scope": event.scope,
                    "revoked_by": event.revoked_by,
                },
            }

        return None

    def query(
        self,
        principal_id: str | None = None,
        event_type: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query audit log entries.

        Args:
            principal_id: Filter by principal.
            event_type: Filter by event type.
            since: Filter entries after this timestamp.
            limit: Maximum entries to return.

        Returns:
            List of matching audit entries.
        """
        with self._lock:
            result = []
            for entry in reversed(self._entries):
                # Apply filters
                if principal_id and entry.get("principal_id") != principal_id:
                    continue
                if event_type and entry.get("event_type") != event_type:
                    continue
                if since and entry.get("timestamp", 0) <= since:
                    continue

                result.append(entry)
                if len(result) >= limit:
                    break

            return result
