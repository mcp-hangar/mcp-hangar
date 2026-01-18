"""Domain contracts for persistence layer.

These protocols define the interfaces that infrastructure must implement,
following the Dependency Inversion Principle (DIP) from SOLID.

The domain layer owns these contracts - infrastructure depends on domain,
not the other way around.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol


class AuditAction(Enum):
    """Types of auditable actions on entities."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    STATE_CHANGED = "state_changed"
    STARTED = "started"
    STOPPED = "stopped"
    DEGRADED = "degraded"
    RECOVERED = "recovered"


@dataclass(frozen=True)
class AuditEntry:
    """Immutable record of an auditable action.

    Value object representing a single audit log entry.
    Immutability ensures audit trail integrity.
    """

    entity_id: str
    entity_type: str
    action: AuditAction
    timestamp: datetime
    actor: str  # who performed the action (system, user, etc.)
    old_state: dict[str, Any] | None = None
    new_state: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for storage."""
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "action": self.action.value,
            "timestamp": self.timestamp.isoformat(),
            "actor": self.actor,
            "old_state": self.old_state,
            "new_state": self.new_state,
            "metadata": self.metadata,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEntry":
        """Deserialize from dictionary."""
        return cls(
            entity_id=data["entity_id"],
            entity_type=data["entity_type"],
            action=AuditAction(data["action"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            actor=data["actor"],
            old_state=data.get("old_state"),
            new_state=data.get("new_state"),
            metadata=data.get("metadata", {}),
            correlation_id=data.get("correlation_id"),
        )


@dataclass(frozen=True)
class ProviderConfigSnapshot:
    """Immutable snapshot of provider configuration.

    Captures the complete configuration state at a point in time,
    used for persistence and recovery.
    """

    provider_id: str
    mode: str
    command: list[str] | None = None
    image: str | None = None
    endpoint: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    idle_ttl_s: int = 300
    health_check_interval_s: int = 60
    max_consecutive_failures: int = 3
    description: str | None = None
    volumes: list[str] = field(default_factory=list)
    build: dict[str, str] | None = None
    resources: dict[str, str] = field(default_factory=dict)
    network: str = "none"
    read_only: bool = True
    user: str | None = None
    tools: list[dict[str, Any]] | None = None
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for storage."""
        return {
            "provider_id": self.provider_id,
            "mode": self.mode,
            "command": self.command,
            "image": self.image,
            "endpoint": self.endpoint,
            "env": self.env,
            "idle_ttl_s": self.idle_ttl_s,
            "health_check_interval_s": self.health_check_interval_s,
            "max_consecutive_failures": self.max_consecutive_failures,
            "description": self.description,
            "volumes": self.volumes,
            "build": self.build,
            "resources": self.resources,
            "network": self.network,
            "read_only": self.read_only,
            "user": self.user,
            "tools": self.tools,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderConfigSnapshot":
        """Deserialize from dictionary."""
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")

        return cls(
            provider_id=data["provider_id"],
            mode=data["mode"],
            command=data.get("command"),
            image=data.get("image"),
            endpoint=data.get("endpoint"),
            env=data.get("env", {}),
            idle_ttl_s=data.get("idle_ttl_s", 300),
            health_check_interval_s=data.get("health_check_interval_s", 60),
            max_consecutive_failures=data.get("max_consecutive_failures", 3),
            description=data.get("description"),
            volumes=data.get("volumes", []),
            build=data.get("build"),
            resources=data.get("resources", {}),
            network=data.get("network", "none"),
            read_only=data.get("read_only", True),
            user=data.get("user"),
            tools=data.get("tools"),
            enabled=data.get("enabled", True),
            created_at=datetime.fromisoformat(created_at) if created_at else None,
            updated_at=datetime.fromisoformat(updated_at) if updated_at else None,
        )


class IProviderConfigRepository(Protocol):
    """Repository protocol for provider configuration persistence.

    Follows Repository pattern from DDD - mediates between domain
    and data mapping layers using a collection-like interface.
    """

    async def save(self, config: ProviderConfigSnapshot) -> None:
        """Save provider configuration.

        Creates or updates the configuration in persistent storage.

        Args:
            config: Provider configuration snapshot to save

        Raises:
            PersistenceError: If save operation fails
        """
        ...

    async def get(self, provider_id: str) -> ProviderConfigSnapshot | None:
        """Retrieve provider configuration by ID.

        Args:
            provider_id: Unique provider identifier

        Returns:
            Configuration snapshot if found, None otherwise
        """
        ...

    async def get_all(self) -> list[ProviderConfigSnapshot]:
        """Retrieve all provider configurations.

        Returns:
            List of all stored configurations
        """
        ...

    async def delete(self, provider_id: str) -> bool:
        """Delete provider configuration.

        Args:
            provider_id: Provider identifier to delete

        Returns:
            True if deleted, False if not found
        """
        ...

    async def exists(self, provider_id: str) -> bool:
        """Check if provider configuration exists.

        Args:
            provider_id: Provider identifier to check

        Returns:
            True if exists, False otherwise
        """
        ...


class IAuditRepository(Protocol):
    """Repository protocol for audit log persistence.

    Provides append-only storage for audit entries, ensuring
    immutable audit trail for accountability.
    """

    async def append(self, entry: AuditEntry) -> None:
        """Append an audit entry.

        Audit entries are immutable once written.

        Args:
            entry: Audit entry to append

        Raises:
            PersistenceError: If append operation fails
        """
        ...

    async def get_by_entity(
        self,
        entity_id: str,
        entity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """Get audit entries for an entity.

        Args:
            entity_id: Entity identifier
            entity_type: Optional entity type filter
            limit: Maximum entries to return
            offset: Number of entries to skip

        Returns:
            List of audit entries, newest first
        """
        ...

    async def get_by_time_range(
        self,
        start: datetime,
        end: datetime,
        entity_type: str | None = None,
        action: AuditAction | None = None,
        limit: int = 1000,
    ) -> list[AuditEntry]:
        """Get audit entries within a time range.

        Args:
            start: Start of time range (inclusive)
            end: End of time range (inclusive)
            entity_type: Optional entity type filter
            action: Optional action filter
            limit: Maximum entries to return

        Returns:
            List of audit entries, newest first
        """
        ...

    async def get_by_correlation_id(self, correlation_id: str) -> list[AuditEntry]:
        """Get all audit entries for a correlation ID.

        Useful for tracing distributed operations.

        Args:
            correlation_id: Correlation identifier

        Returns:
            List of related audit entries
        """
        ...


class IUnitOfWork(Protocol):
    """Unit of Work pattern for transactional consistency.

    Manages transactions across multiple repositories,
    ensuring atomic commits or rollbacks.
    """

    async def __aenter__(self) -> "IUnitOfWork":
        """Begin transaction."""
        ...

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """End transaction - commit on success, rollback on exception."""
        ...

    async def commit(self) -> None:
        """Explicitly commit the transaction."""
        ...

    async def rollback(self) -> None:
        """Explicitly rollback the transaction."""
        ...

    @property
    def providers(self) -> IProviderConfigRepository:
        """Access provider config repository within transaction."""
        ...

    @property
    def audit(self) -> IAuditRepository:
        """Access audit repository within transaction."""
        ...


class IRecoveryService(Protocol):
    """Service protocol for system recovery on startup.

    Responsible for restoring system state from persistent storage.
    """

    async def recover_providers(self) -> list[str]:
        """Recover all provider configurations from storage.

        Loads saved configurations and registers them with
        the provider repository.

        Returns:
            List of recovered provider IDs
        """
        ...

    async def get_recovery_status(self) -> dict[str, Any]:
        """Get status of last recovery operation.

        Returns:
            Dictionary with recovery metrics and status
        """
        ...


class PersistenceError(Exception):
    """Base exception for persistence operations."""

    pass


class ConfigurationNotFoundError(PersistenceError):
    """Raised when configuration is not found."""

    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        super().__init__(f"Configuration not found for provider: {provider_id}")


class ConcurrentModificationError(PersistenceError):
    """Raised when concurrent modification is detected."""

    def __init__(self, provider_id: str, expected_version: int, actual_version: int):
        self.provider_id = provider_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Concurrent modification on provider '{provider_id}': "
            f"expected version {expected_version}, actual {actual_version}"
        )
