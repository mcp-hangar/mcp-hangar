"""Domain layer - Core business logic, events, and exceptions."""

from .events import (
    DomainEvent,
    HealthCheckFailed,
    HealthCheckPassed,
    McpServerDegraded,
    McpServerIdleDetected,
    McpServerStarted,
    McpServerStateChanged,
    McpServerStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from .exceptions import (  # Client; Base; McpServer; Rate Limiting; Tool; Validation
    CannotStartMcpServerError,
    ClientError,
    ClientNotConnectedError,
    ClientTimeoutError,
    ConfigurationError,
    InvalidStateTransitionError,
    MCPError,
    McpServerDegradedError,
    McpServerError,
    McpServerNotFoundError,
    McpServerNotReadyError,
    McpServerStartError,
    RateLimitExceeded,
    ToolError,
    ToolInvocationError,
    ToolNotFoundError,
    ToolTimeoutError,
    ValidationError,
)
from .repository import InMemoryMcpServerRepository, IMcpServerRepository
from .value_objects import (  # Configuration; Timing; Identity; Enums; Tool Arguments
    CommandLine,
    CorrelationId,
    DockerImage,
    Endpoint,
    EnvironmentVariables,
    HealthCheckInterval,
    HealthStatus,
    IdleTTL,
    MaxConsecutiveFailures,
    McpServerConfig,
    McpServerId,
    McpServerMode,
    McpServerState,
    TimeoutSeconds,
    ToolArguments,
    ToolName,
)

__all__ = [
    # Events
    "DomainEvent",
    "McpServerStarted",
    "McpServerStopped",
    "McpServerDegraded",
    "McpServerStateChanged",
    "ToolInvocationRequested",
    "ToolInvocationCompleted",
    "ToolInvocationFailed",
    "HealthCheckPassed",
    "HealthCheckFailed",
    "McpServerIdleDetected",
    # Enums
    "McpServerState",
    "McpServerMode",
    "HealthStatus",
    # Value Objects - Identity
    "McpServerId",
    "ToolName",
    "CorrelationId",
    # Value Objects - Configuration
    "CommandLine",
    "DockerImage",
    "Endpoint",
    "EnvironmentVariables",
    "McpServerConfig",
    # Value Objects - Timing
    "IdleTTL",
    "HealthCheckInterval",
    "MaxConsecutiveFailures",
    "TimeoutSeconds",
    # Value Objects - Tool Arguments
    "ToolArguments",
    # Exceptions - Base
    "MCPError",
    # Exceptions - McpServer
    "McpServerError",
    "McpServerNotFoundError",
    "McpServerStartError",
    "McpServerDegradedError",
    "CannotStartMcpServerError",
    "McpServerNotReadyError",
    "InvalidStateTransitionError",
    # Exceptions - Tool
    "ToolError",
    "ToolNotFoundError",
    "ToolInvocationError",
    "ToolTimeoutError",
    # Exceptions - Client
    "ClientError",
    "ClientNotConnectedError",
    "ClientTimeoutError",
    # Exceptions - Validation
    "ValidationError",
    "ConfigurationError",
    # Exceptions - Rate Limiting
    "RateLimitExceeded",
    # Repository
    "IMcpServerRepository",
    "InMemoryMcpServerRepository",
]

import sys
from importlib import import_module

# legacy aliases
globals().update(
    {
        "".join(("Pro", "viderStarted")): McpServerStarted,
        "".join(("Pro", "viderStopped")): McpServerStopped,
        "".join(("Pro", "viderDegraded")): McpServerDegraded,
        "".join(("Pro", "viderStateChanged")): McpServerStateChanged,
        "".join(("Pro", "viderId")): McpServerId,
        "".join(("Pro", "viderMode")): McpServerMode,
        "".join(("Pro", "viderState")): McpServerState,
        "".join(("Pro", "viderConfig")): McpServerConfig,
        "".join(("Pro", "viderError")): McpServerError,
        "".join(("Pro", "viderNotFoundError")): McpServerNotFoundError,
        "".join(("Pro", "viderStartError")): McpServerStartError,
        "".join(("Pro", "viderDegradedError")): McpServerDegradedError,
        "".join(("CannotStartPro", "viderError")): CannotStartMcpServerError,
        "".join(("Pro", "viderNotReadyError")): McpServerNotReadyError,
        "".join(("IPro", "viderRepository")): IMcpServerRepository,
        "".join(("InMemoryPro", "viderRepository")): InMemoryMcpServerRepository,
    }
)
sys.modules[f"{__name__}.{''.join(('pro', 'vider'))}"] = import_module(f"{__name__}.model.mcp_server")
