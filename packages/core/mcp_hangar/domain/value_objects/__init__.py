"""Value Objects for the MCP Hangar domain.

Value objects are immutable, validated domain primitives that encapsulate
business rules and prevent invalid states. They replace primitive obsession
with strongly-typed domain concepts.

This module is organized into themed submodules:
- security.py: Authentication and authorization (Principal, Permission, Role)
- provider.py: Provider lifecycle (ProviderState, ProviderMode, ProviderId)
- health.py: Health status (HealthStatus, HealthCheckInterval)
- config.py: Configuration (CommandLine, DockerImage, Endpoint, etc.)
- common.py: Common types (CorrelationId, ToolName, ToolArguments, tenancy)

All types are re-exported here for backward compatibility.
"""

# Common / shared value objects
from .common import CatalogItemId, CorrelationId, NamespaceId, ResourceScope, TenantId, ToolArguments, ToolName

# Configuration
from .config import (
    CommandLine,
    DockerImage,
    Endpoint,
    EnvironmentVariables,
    HttpAuthConfig,
    HttpAuthType,
    HttpTlsConfig,
    HttpTransportConfig,
    IdleTTL,
    MaxConsecutiveFailures,
    TimeoutSeconds,
)

# Health status
from .health import HealthCheckInterval, HealthStatus

# Provider lifecycle and identity
from .provider import (
    GroupId,
    GroupState,
    LoadBalancerStrategy,
    MemberPriority,
    MemberWeight,
    ProviderConfig,
    ProviderId,
    ProviderMode,
    ProviderState,
)

# Security - Authentication & Authorization
from .security import Permission, Principal, PrincipalId, PrincipalType, Role

__all__ = [
    # Security
    "PrincipalType",
    "PrincipalId",
    "Principal",
    "Permission",
    "Role",
    # Provider
    "ProviderState",
    "ProviderMode",
    "ProviderId",
    "ProviderConfig",
    "LoadBalancerStrategy",
    "GroupState",
    "GroupId",
    "MemberWeight",
    "MemberPriority",
    # Health
    "HealthStatus",
    "HealthCheckInterval",
    # Configuration
    "CommandLine",
    "DockerImage",
    "Endpoint",
    "EnvironmentVariables",
    "IdleTTL",
    "MaxConsecutiveFailures",
    "TimeoutSeconds",
    "HttpAuthType",
    "HttpAuthConfig",
    "HttpTlsConfig",
    "HttpTransportConfig",
    # Common
    "ToolName",
    "CorrelationId",
    "ToolArguments",
    "TenantId",
    "NamespaceId",
    "CatalogItemId",
    "ResourceScope",
]
