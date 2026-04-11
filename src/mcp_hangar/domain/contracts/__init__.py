"""Domain contracts - interfaces for external dependencies.

This module defines contracts (abstract interfaces) that the domain layer
depends on. Implementations are provided by the infrastructure layer.
"""

from .authentication import (
    ApiKeyMetadata,
    AuthRequest,
    IApiKeyStore,
    IAuthenticator,
    ITokenValidator,
    NullApiKeyStore,
    NullAuthenticator,
)
from .behavioral import (
    IBehavioralProfiler,
    IBaselineStore,
    IDeviationDetector,
    NullBehavioralProfiler,
)
from .command import CommandHandler
from .event_bus import IEventBus
from .runtime_store import IRuntimeProviderStore
from .authorization import (
    AuthorizationRequest,
    AuthorizationResult,
    IAuthorizer,
    IPolicyEngine,
    IRoleStore,
    IToolAccessPolicyEnforcer,
    IToolAccessPolicyStore,
    NullAuthorizer,
    NullRoleStore,
    NullToolAccessPolicyEnforcer,
    NullToolAccessPolicyStore,
    PolicyEvaluationResult,
)
from .event_store import ConcurrencyError, IDurableEventStore, IEventStore, NullEventStore, StreamNotFoundError
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
    # Authentication contracts
    "ApiKeyMetadata",
    "AuthRequest",
    "IApiKeyStore",
    "IAuthenticator",
    "ITokenValidator",
    "NullApiKeyStore",
    "NullAuthenticator",
    # Behavioral profiling contracts
    "IBehavioralProfiler",
    "IBaselineStore",
    "IDeviationDetector",
    "NullBehavioralProfiler",
    # Authorization contracts
    "AuthorizationRequest",
    "AuthorizationResult",
    "IAuthorizer",
    "IPolicyEngine",
    "IRoleStore",
    "IToolAccessPolicyEnforcer",
    "IToolAccessPolicyStore",
    "NullAuthorizer",
    "NullRoleStore",
    "NullToolAccessPolicyEnforcer",
    "NullToolAccessPolicyStore",
    "PolicyEvaluationResult",
    # Command handler
    "CommandHandler",
    # Event bus
    "IEventBus",
    # Event store
    "ConcurrencyError",
    "IDurableEventStore",
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
