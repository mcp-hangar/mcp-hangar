"""Domain contracts - interfaces for external dependencies.

This module defines contracts (abstract interfaces) that the domain layer
depends on. Implementations are provided by the infrastructure layer.
"""

from .authentication import ApiKeyMetadata, AuthRequest, IApiKeyStore, IAuthenticator, ITokenValidator
from .authorization import AuthorizationRequest, AuthorizationResult, IAuthorizer, IPolicyEngine, IRoleStore
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
    # Authentication contracts
    "ApiKeyMetadata",
    "AuthRequest",
    "IApiKeyStore",
    "IAuthenticator",
    "ITokenValidator",
    # Authorization contracts
    "AuthorizationRequest",
    "AuthorizationResult",
    "IAuthorizer",
    "IPolicyEngine",
    "IRoleStore",
    # Event store
    "ConcurrencyError",
    "IEventStore",
    "NullEventStore",
    "StreamNotFoundError",
    # Metrics
    "IMetricsPublisher",
    # Persistence
    "AuditAction",
    "AuditEntry",
    "ConcurrentModificationError",
    "ConfigurationNotFoundError",
    "IAuditRepository",
    "IProviderConfigRepository",
    "IRecoveryService",
    "IUnitOfWork",
    "PersistenceError",
    "ProviderConfigSnapshot",
    "ProviderRuntime",
]
