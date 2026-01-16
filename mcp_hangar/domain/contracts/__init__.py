"""Domain contracts - interfaces for external dependencies.

This module defines contracts (abstract interfaces) that the domain layer
depends on. Implementations are provided by the infrastructure layer.
"""

from .event_store import ConcurrencyError, IEventStore, NullEventStore, StreamNotFoundError
from .metrics_publisher import IMetricsPublisher
from .persistence import (
    AuditAction,
    AuditEntry,
    ConcurrentModificationError,
    ConfigurationNotFoundError,
    IAuditRepository,
    IProviderConfigRepository,
    IRecoveryService,
    IUnitOfWork,
    PersistenceError,
    ProviderConfigSnapshot,
)
from .provider_runtime import ProviderRuntime

__all__ = [
    "AuditAction",
    "AuditEntry",
    "ConcurrencyError",
    "ConcurrentModificationError",
    "ConfigurationNotFoundError",
    "IAuditRepository",
    "IEventStore",
    "IMetricsPublisher",
    "IProviderConfigRepository",
    "IRecoveryService",
    "IUnitOfWork",
    "NullEventStore",
    "PersistenceError",
    "ProviderConfigSnapshot",
    "ProviderRuntime",
    "StreamNotFoundError",
]
