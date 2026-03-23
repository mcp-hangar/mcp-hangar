"""Domain contracts - interfaces for external dependencies.

This module defines contracts (abstract interfaces) that the domain layer
depends on. Implementations are provided by the infrastructure layer.
"""

from .catalog import McpCatalogRepository
from .authentication import ApiKeyMetadata, AuthRequest, IApiKeyStore, IAuthenticator, ITokenValidator
from .command import CommandHandler
from .event_bus import IEventBus
from .runtime_store import IRuntimeProviderStore
from .authorization import AuthorizationRequest, AuthorizationResult, IAuthorizer, IPolicyEngine, IRoleStore
from .event_store import ConcurrencyError, IEventStore, NullEventStore, StreamNotFoundError
from .installer import InstalledPackage, IPackageInstaller
from .log_buffer import IProviderLogBuffer
from .metrics_publisher import IMetricsPublisher
from .persistence import (
    AuditAction,
    AuditEntry,
    ConcurrentModificationError,
    ConfigurationNotFoundError,
    IAuditRepository,
    IProviderConfigRepository,
    PersistenceError,
    ProviderConfigSnapshot,
)
from .provider_runtime import ProviderRuntime
from .registry import IRegistryClient, PackageInfo, ServerDetails, ServerSummary, TransportInfo
from .response_cache import CacheRetrievalResult, IResponseCache, NullResponseCache

__all__ = [
    # Catalog contracts
    "McpCatalogRepository",
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
    # Command handler
    "CommandHandler",
    # Event bus
    "IEventBus",
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
    "PersistenceError",
    "ProviderConfigSnapshot",
    "ProviderRuntime",
    # Registry contracts
    "IRegistryClient",
    "PackageInfo",
    "ServerDetails",
    "ServerSummary",
    "TransportInfo",
    # Response cache contracts
    "CacheRetrievalResult",
    "IResponseCache",
    "NullResponseCache",
    # Log buffer
    "IProviderLogBuffer",
    # Runtime store
    "IRuntimeProviderStore",
]
