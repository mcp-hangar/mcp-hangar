"""Knowledge Base abstractions and contracts.

Defines interfaces for knowledge base operations that can be implemented
by different backends (PostgreSQL, SQLite, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class KnowledgeBaseDriver(Enum):
    """Supported knowledge base drivers."""

    POSTGRES = "postgres"
    SQLITE = "sqlite"
    MEMORY = "memory"  # For testing


@dataclass
class KnowledgeBaseConfig:
    """Knowledge base configuration."""

    enabled: bool = False
    driver: KnowledgeBaseDriver = KnowledgeBaseDriver.SQLITE
    dsn: str = ""
    pool_size: int = 5
    cache_ttl_s: int = 300

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeBaseConfig":
        """Create config from dictionary."""
        if not data.get("enabled", False):
            return cls(enabled=False)

        dsn = data.get("dsn", "")

        # Auto-detect driver from DSN
        if dsn.startswith("postgresql://") or dsn.startswith("postgres://"):
            driver = KnowledgeBaseDriver.POSTGRES
        elif dsn.startswith("sqlite://") or dsn.endswith(".db"):
            driver = KnowledgeBaseDriver.SQLITE
        else:
            # Explicit driver override
            driver_str = data.get("driver", "sqlite")
            driver = KnowledgeBaseDriver(driver_str)

        return cls(
            enabled=True,
            driver=driver,
            dsn=dsn,
            pool_size=data.get("pool_size", 5),
            cache_ttl_s=data.get("cache_ttl_s", 300),
        )


@dataclass
class AuditEntry:
    """Audit log entry."""

    event_type: str
    provider: str | None = None
    tool: str | None = None
    arguments: dict | None = None
    result_summary: str | None = None
    duration_ms: int | None = None
    success: bool = True
    error_message: str | None = None
    correlation_id: str | None = None
    timestamp: datetime | None = None


@dataclass
class ProviderStateEntry:
    """Provider state history entry."""

    provider_id: str
    old_state: str | None
    new_state: str
    reason: str | None = None
    timestamp: datetime | None = None


@dataclass
class MetricEntry:
    """Provider metric entry."""

    provider_id: str
    metric_name: str
    metric_value: float
    labels: dict | None = None
    timestamp: datetime | None = None


class IKnowledgeBase(ABC):
    """Abstract interface for knowledge base operations.

    Implementations must be thread-safe and handle their own connection management.
    """

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the knowledge base (create tables, run migrations).

        Returns True if successful.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close connections and cleanup resources."""
        pass

    @abstractmethod
    async def is_healthy(self) -> bool:
        """Check if knowledge base is operational."""
        pass

    # === Cache Operations ===

    @abstractmethod
    async def cache_get(self, provider: str, tool: str, arguments: dict) -> dict | None:
        """Get cached tool result. Returns None if not found or expired."""
        pass

    @abstractmethod
    async def cache_set(
        self,
        provider: str,
        tool: str,
        arguments: dict,
        result: Any,
        ttl_s: int | None = None,
    ) -> bool:
        """Cache tool result. Returns True if successful."""
        pass

    @abstractmethod
    async def cache_invalidate(self, provider: str | None = None, tool: str | None = None) -> int:
        """Invalidate cache entries. Returns count of invalidated entries."""
        pass

    @abstractmethod
    async def cache_cleanup(self) -> int:
        """Remove expired cache entries. Returns count of removed entries."""
        pass

    # === Audit Operations ===

    @abstractmethod
    async def audit_log(self, entry: AuditEntry) -> bool:
        """Log audit entry. Returns True if successful."""
        pass

    @abstractmethod
    async def audit_query(
        self,
        provider: str | None = None,
        tool: str | None = None,
        success: bool | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Query audit log entries."""
        pass

    @abstractmethod
    async def audit_stats(self, hours: int = 24) -> dict:
        """Get audit statistics for last N hours."""
        pass

    # === Provider State Operations ===

    @abstractmethod
    async def record_state_change(self, entry: ProviderStateEntry) -> bool:
        """Record provider state change. Returns True if successful."""
        pass

    @abstractmethod
    async def get_state_history(self, provider_id: str, limit: int = 100) -> list[ProviderStateEntry]:
        """Get provider state history."""
        pass

    # === Metrics Operations ===

    @abstractmethod
    async def record_metric(self, entry: MetricEntry) -> bool:
        """Record provider metric. Returns True if successful."""
        pass

    @abstractmethod
    async def get_metrics(
        self,
        provider_id: str,
        metric_name: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[MetricEntry]:
        """Get provider metrics."""
        pass
