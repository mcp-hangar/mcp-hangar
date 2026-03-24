"""Event Sourced API Key aggregate.

Implements Event Sourcing pattern for API keys where:
- State is derived from events, not stored directly
- All changes are captured as immutable events
- State can be rebuilt by replaying events
- Supports snapshots for performance
"""

from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

from ..events import ApiKeyCreated, ApiKeyRevoked, KeyRotated, DomainEvent
from ..value_objects import Principal, PrincipalId, PrincipalType
from .aggregate import AggregateRoot


@dataclass
class ApiKeySnapshot:
    """Snapshot of API key state for faster loading.

    Attributes:
        key_hash: SHA-256 hash of the key.
        key_id: Unique identifier.
        principal_id: Principal this key authenticates as.
        name: Human-readable name.
        tenant_id: Optional tenant ID.
        groups: Groups assigned to the principal.
        created_at: Creation timestamp.
        expires_at: Optional expiration timestamp.
        last_used_at: Last usage timestamp.
        revoked: Whether the key is revoked.
        revoked_at: When the key was revoked.
        rotated_to_key_id: Key ID this was rotated to (if rotated).
        grace_until: Timestamp when grace period expires.
        version: Aggregate version.
    """

    key_hash: str
    key_id: str
    principal_id: str
    name: str
    tenant_id: str | None
    groups: list[str]
    created_at: float
    expires_at: float | None
    last_used_at: float | None
    revoked: bool
    revoked_at: float | None
    rotated_to_key_id: str | None
    grace_until: float | None
    version: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "key_hash": self.key_hash,
            "key_id": self.key_id,
            "principal_id": self.principal_id,
            "name": self.name,
            "tenant_id": self.tenant_id,
            "groups": self.groups,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
            "revoked": self.revoked,
            "revoked_at": self.revoked_at,
            "rotated_to_key_id": self.rotated_to_key_id,
            "grace_until": self.grace_until,
            "version": self.version,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ApiKeySnapshot":
        """Create from dictionary."""
        return cls(
            key_hash=d["key_hash"],
            key_id=d["key_id"],
            principal_id=d["principal_id"],
            name=d["name"],
            tenant_id=d.get("tenant_id"),
            groups=d.get("groups", []),
            created_at=d["created_at"],
            expires_at=d.get("expires_at"),
            last_used_at=d.get("last_used_at"),
            revoked=d.get("revoked", False),
            revoked_at=d.get("revoked_at"),
            rotated_to_key_id=d.get("rotated_to_key_id"),
            grace_until=d.get("grace_until"),
            version=d.get("version", 0),
            metadata=d.get("metadata", {}),
        )


class EventSourcedApiKey(AggregateRoot):
    """Event Sourced API Key aggregate.

    All state changes are recorded as events and state is rebuilt
    by replaying those events. This provides:
    - Complete audit trail
    - Time-travel debugging
    - Event-driven integrations
    """

    def __init__(
        self,
        key_hash: str,
        key_id: str,
        principal_id: str,
        name: str,
        tenant_id: str | None = None,
        groups: frozenset[str] | None = None,
        expires_at: datetime | None = None,
    ):
        """Initialize a new API key aggregate.

        Note: This doesn't record creation event - use create() factory method.
        """
        super().__init__()

        # Identity
        self._key_hash = key_hash
        self._key_id = key_id

        # Principal info
        self._principal_id = principal_id
        self._name = name
        self._tenant_id = tenant_id
        self._groups = groups or frozenset()

        # Timestamps
        self._created_at: datetime | None = None
        self._expires_at = expires_at
        self._last_used_at: datetime | None = None

        # State
        self._revoked = False
        self._revoked_at: datetime | None = None
        self._metadata: dict[str, Any] = {}

        # Rotation state
        self._rotated_to_key_id: str | None = None
        self._grace_until: datetime | None = None

    @property
    def key_hash(self) -> str:
        return self._key_hash

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def principal_id(self) -> str:
        return self._principal_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def tenant_id(self) -> str | None:
        return self._tenant_id

    @property
    def groups(self) -> frozenset[str]:
        return self._groups

    @property
    def created_at(self) -> datetime | None:
        return self._created_at

    @property
    def expires_at(self) -> datetime | None:
        return self._expires_at

    @property
    def last_used_at(self) -> datetime | None:
        return self._last_used_at

    @property
    def is_revoked(self) -> bool:
        return self._revoked

    @property
    def is_expired(self) -> bool:
        if self._expires_at is None:
            return False
        return datetime.now(UTC) > self._expires_at

    @property
    def is_rotated(self) -> bool:
        """Check if this key has been rotated."""
        return self._rotated_to_key_id is not None

    @property
    def grace_until(self) -> datetime | None:
        """Get grace period expiration time."""
        return self._grace_until

    @property
    def is_in_grace_period(self) -> bool:
        """Check if rotated key is still within grace period."""
        if not self.is_rotated or self._grace_until is None:
            return False
        return datetime.now(UTC) < self._grace_until

    @property
    def is_valid(self) -> bool:
        """Check if key is valid for authentication.

        Valid if: not revoked AND not expired AND (not rotated OR in grace period)
        """
        if self._revoked or self.is_expired:
            return False
        if self.is_rotated and not self.is_in_grace_period:
            return False
        return True

    # =========================================================================
    # Factory Methods
    # =========================================================================

    @classmethod
    def create(
        cls,
        key_hash: str,
        key_id: str,
        principal_id: str,
        name: str,
        created_by: str,
        tenant_id: str | None = None,
        groups: frozenset[str] | None = None,
        expires_at: datetime | None = None,
    ) -> "EventSourcedApiKey":
        """Create a new API key and record creation event.

        Args:
            key_hash: SHA-256 hash of the raw key.
            key_id: Unique identifier for management.
            principal_id: Principal this key authenticates as.
            name: Human-readable name.
            created_by: Who created this key.
            tenant_id: Optional tenant for multi-tenancy.
            groups: Optional groups for the principal.
            expires_at: Optional expiration datetime.

        Returns:
            New EventSourcedApiKey with ApiKeyCreated event recorded.
        """
        key = cls(
            key_hash=key_hash,
            key_id=key_id,
            principal_id=principal_id,
            name=name,
            tenant_id=tenant_id,
            groups=groups,
            expires_at=expires_at,
        )

        # Record creation event
        key._record_event(
            ApiKeyCreated(
                key_id=key_id,
                principal_id=principal_id,
                key_name=name,
                expires_at=expires_at.timestamp() if expires_at else None,
                created_by=created_by,
            )
        )

        # Apply the event to set state
        key._created_at = datetime.now(UTC)

        return key

    @classmethod
    def from_events(
        cls,
        key_hash: str,
        key_id: str,
        principal_id: str,
        name: str,
        events: list[DomainEvent],
        tenant_id: str | None = None,
        groups: frozenset[str] | None = None,
        expires_at: datetime | None = None,
    ) -> "EventSourcedApiKey":
        """Rebuild API key state from events.

        Args:
            key_hash: SHA-256 hash of the key.
            key_id: Unique identifier.
            principal_id: Principal ID.
            name: Key name.
            events: Events to replay.
            tenant_id: Optional tenant.
            groups: Optional groups.
            expires_at: Optional expiration.

        Returns:
            EventSourcedApiKey with state rebuilt from events.
        """
        key = cls(
            key_hash=key_hash,
            key_id=key_id,
            principal_id=principal_id,
            name=name,
            tenant_id=tenant_id,
            groups=groups,
            expires_at=expires_at,
        )

        for event in events:
            key._apply_event(event)

        return key

    @classmethod
    def from_snapshot(
        cls,
        snapshot: ApiKeySnapshot,
        events: list[DomainEvent] | None = None,
    ) -> "EventSourcedApiKey":
        """Load API key from snapshot and optional subsequent events.

        Args:
            snapshot: Snapshot to load from.
            events: Optional events after snapshot.

        Returns:
            EventSourcedApiKey with state from snapshot + events.
        """
        key = cls(
            key_hash=snapshot.key_hash,
            key_id=snapshot.key_id,
            principal_id=snapshot.principal_id,
            name=snapshot.name,
            tenant_id=snapshot.tenant_id,
            groups=frozenset(snapshot.groups),
            expires_at=datetime.fromtimestamp(snapshot.expires_at, tz=UTC) if snapshot.expires_at else None,
        )

        # Restore state from snapshot
        key._created_at = datetime.fromtimestamp(snapshot.created_at, tz=UTC)
        key._last_used_at = datetime.fromtimestamp(snapshot.last_used_at, tz=UTC) if snapshot.last_used_at else None
        key._revoked = snapshot.revoked
        key._revoked_at = datetime.fromtimestamp(snapshot.revoked_at, tz=UTC) if snapshot.revoked_at else None
        key._rotated_to_key_id = snapshot.rotated_to_key_id
        key._grace_until = datetime.fromtimestamp(snapshot.grace_until, tz=UTC) if snapshot.grace_until else None
        key._metadata = dict(snapshot.metadata)
        key._version = snapshot.version

        # Apply any events after snapshot
        if events:
            for event in events:
                key._apply_event(event)

        return key

    # =========================================================================
    # Commands (mutate state via events)
    # =========================================================================

    def revoke(self, revoked_by: str, reason: str = "") -> None:
        """Revoke this API key.

        Args:
            revoked_by: Principal revoking the key.
            reason: Optional reason for revocation.

        Raises:
            ValueError: If key is already revoked.
        """
        if self._revoked:
            raise ValueError(f"API key {self._key_id} is already revoked")

        self._record_event(
            ApiKeyRevoked(
                key_id=self._key_id,
                principal_id=self._principal_id,
                revoked_by=revoked_by,
                reason=reason,
            )
        )

        # Apply immediately
        self._revoked = True
        self._revoked_at = datetime.now(UTC)

    def record_usage(self) -> None:
        """Record that this key was used for authentication."""
        self._last_used_at = datetime.now(UTC)

    def rotate(self, new_key_id: str, grace_until: datetime, rotated_by: str) -> None:
        """Rotate this API key.

        Args:
            new_key_id: The key_id of the new key.
            grace_until: When the old key becomes invalid.
            rotated_by: Principal initiating the rotation.

        Raises:
            ValueError: If key is revoked or already has pending rotation.
        """
        if self._revoked:
            raise ValueError(f"Cannot rotate revoked key {self._key_id}")

        if self._rotated_to_key_id is not None and self._grace_until and datetime.now(UTC) < self._grace_until:
            raise ValueError(f"Key {self._key_id} already has pending rotation")

        now = datetime.now(UTC)

        self._record_event(
            KeyRotated(
                key_id=self._key_id,
                principal_id=self._principal_id,
                new_key_id=new_key_id,
                rotated_at=now.timestamp(),
                grace_until=grace_until.timestamp(),
                rotated_by=rotated_by,
            )
        )

        # Apply immediately
        self._rotated_to_key_id = new_key_id
        self._grace_until = grace_until

    # =========================================================================
    # Event Application
    # =========================================================================

    def _apply_event(self, event: DomainEvent) -> None:
        """Apply an event to update state.

        This is called when replaying events to rebuild state.
        """
        if isinstance(event, ApiKeyCreated):
            self._created_at = datetime.fromtimestamp(event.occurred_at, tz=UTC)

        elif isinstance(event, ApiKeyRevoked):
            self._revoked = True
            self._revoked_at = datetime.fromtimestamp(event.occurred_at, tz=UTC)

        elif isinstance(event, KeyRotated):
            self._rotated_to_key_id = event.new_key_id
            self._grace_until = datetime.fromtimestamp(event.grace_until, tz=UTC)

        self._version += 1

    # =========================================================================
    # Queries
    # =========================================================================

    def to_principal(self) -> Principal:
        """Convert to Principal for authentication."""
        return Principal(
            id=PrincipalId(self._principal_id),
            type=PrincipalType.SERVICE_ACCOUNT,
            tenant_id=self._tenant_id,
            groups=self._groups,
            metadata={
                "key_id": self._key_id,
                "key_name": self._name,
                **self._metadata,
            },
        )

    def create_snapshot(self) -> ApiKeySnapshot:
        """Create a snapshot of current state."""
        return ApiKeySnapshot(
            key_hash=self._key_hash,
            key_id=self._key_id,
            principal_id=self._principal_id,
            name=self._name,
            tenant_id=self._tenant_id,
            groups=list(self._groups),
            created_at=self._created_at.timestamp() if self._created_at else 0,
            expires_at=self._expires_at.timestamp() if self._expires_at else None,
            last_used_at=self._last_used_at.timestamp() if self._last_used_at else None,
            revoked=self._revoked,
            revoked_at=self._revoked_at.timestamp() if self._revoked_at else None,
            rotated_to_key_id=self._rotated_to_key_id,
            grace_until=self._grace_until.timestamp() if self._grace_until else None,
            version=self._version,
            metadata=dict(self._metadata),
        )
