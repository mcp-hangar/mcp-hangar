"""Domain contracts - interfaces for external dependencies.

This module defines contracts (abstract interfaces) that the domain layer
depends on. Implementations are provided by the infrastructure layer.
"""

from .authentication import ApiKeyMetadata, AuthRequest, IApiKeyStore, IAuthenticator, ITokenValidator
from .authorization import AuthorizationRequest, AuthorizationResult, IAuthorizer, IPolicyEngine, IRoleStore
from .event_store import ConcurrencyError, IEventStore, NullEventStore, StreamNotFoundError
from .installer import InstalledPackage, IPackageInstaller
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
from .registry import IRegistryClient, PackageInfo, ServerDetails, ServerSummary, TransportInfo

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
    # Installer contracts
    "IPackageInstaller",
    "InstalledPackage",
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
    # Registry contracts
    "IRegistryClient",
    "PackageInfo",
    "ServerDetails",
    "ServerSummary",
    "TransportInfo",
]
