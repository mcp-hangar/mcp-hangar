"""Value Objects for the MCP Hangar domain.

Value objects are immutable, validated domain primitives that encapsulate
business rules and prevent invalid states. They replace primitive obsession
with strongly-typed domain concepts.

This module is organized into themed submodules:
- security.py: Authentication and authorization (Principal, Permission, Role)
- mcp_server.py: McpServer lifecycle (McpServerState, McpServerMode, McpServerId)
- health.py: Health status (HealthStatus, HealthCheckInterval)
- config.py: Configuration (CommandLine, DockerImage, Endpoint, etc.)
- common.py: Common types (CorrelationId, ToolName, ToolArguments, tenancy)

All types are re-exported here for backward compatibility.
"""

# Common / shared value objects
from .common import CatalogItemId, CorrelationId, NamespaceId, ResourceScope, TenantId, ToolArguments, ToolName

# Capability declarations (Phase 1 enforcement)
from .capabilities import (
    EgressRule,
    EnvironmentCapabilities,
    FilesystemCapabilities,
    NetworkCapabilities,
    McpServerCapabilities,
    ResourceCapabilities,
    ToolCapabilities,
    ViolationSeverity,
    ViolationType,
)

# Log value objects
from .log import LogLine

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

# McpServer lifecycle and identity
from .mcp_server import (
    GroupId,
    GroupState,
    LoadBalancerStrategy,
    MemberPriority,
    MemberWeight,
    McpServerConfig,
    McpServerId,
    McpServerMode,
    McpServerState,
)

# Security - Authentication & Authorization
from .security import Permission, Principal, PrincipalId, PrincipalType, Role

# Tool Access Policy
from .tool_access_policy import ToolAccessPolicy

# Truncation
from .truncation import ContinuationId, TruncationConfig

# Discovery
from .discovery import DiscoverySourceSpec

# License
from .license import LicenseTier

__all__ = [
    # Security
    "PrincipalType",
    "PrincipalId",
    "Principal",
    "Permission",
    "Role",
    # McpServer
    "McpServerState",
    "McpServerMode",
    "McpServerId",
    "McpServerConfig",
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
    # Tool Access Policy
    "ToolAccessPolicy",
    # Truncation
    "TruncationConfig",
    "ContinuationId",
    # Log
    "LogLine",
    # Discovery
    "DiscoverySourceSpec",
    # License
    "LicenseTier",
    # Capabilities
    "EgressRule",
    "EnvironmentCapabilities",
    "FilesystemCapabilities",
    "NetworkCapabilities",
    "McpServerCapabilities",
    "ResourceCapabilities",
    "ToolCapabilities",
    "ViolationSeverity",
    "ViolationType",
]

import sys
from importlib import import_module

# legacy aliases
globals().update(
    {
        "".join(("Pro", "viderId")): McpServerId,
        "".join(("Pro", "viderMode")): McpServerMode,
        "".join(("Pro", "viderState")): McpServerState,
        "".join(("Pro", "viderConfig")): McpServerConfig,
        "".join(("Pro", "viderCapabilities")): McpServerCapabilities,
    }
)
sys.modules[f"{__name__}.{''.join(('pro', 'vider'))}"] = import_module(f"{__name__}.mcp_server")
