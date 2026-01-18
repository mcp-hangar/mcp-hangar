"""Domain layer - Core business logic, events, and exceptions."""

from .events import (
    DomainEvent,
    HealthCheckFailed,
    HealthCheckPassed,
    ProviderDegraded,
    ProviderIdleDetected,
    ProviderStarted,
    ProviderStateChanged,
    ProviderStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from .exceptions import (  # Client; Base; Provider; Rate Limiting; Tool; Validation
    CannotStartProviderError,
    ClientError,
    ClientNotConnectedError,
    ClientTimeoutError,
    ConfigurationError,
    InvalidStateTransitionError,
    MCPError,
    ProviderDegradedError,
    ProviderError,
    ProviderNotFoundError,
    ProviderNotReadyError,
    ProviderStartError,
    RateLimitExceeded,
    ToolError,
    ToolInvocationError,
    ToolNotFoundError,
    ToolTimeoutError,
    ValidationError,
)
from .repository import InMemoryProviderRepository, IProviderRepository
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
    ProviderConfig,
    ProviderId,
    ProviderMode,
    ProviderState,
    TimeoutSeconds,
    ToolArguments,
    ToolName,
)

__all__ = [
    # Events
    "DomainEvent",
    "ProviderStarted",
    "ProviderStopped",
    "ProviderDegraded",
    "ProviderStateChanged",
    "ToolInvocationRequested",
    "ToolInvocationCompleted",
    "ToolInvocationFailed",
    "HealthCheckPassed",
    "HealthCheckFailed",
    "ProviderIdleDetected",
    # Enums
    "ProviderState",
    "ProviderMode",
    "HealthStatus",
    # Value Objects - Identity
    "ProviderId",
    "ToolName",
    "CorrelationId",
    # Value Objects - Configuration
    "CommandLine",
    "DockerImage",
    "Endpoint",
    "EnvironmentVariables",
    "ProviderConfig",
    # Value Objects - Timing
    "IdleTTL",
    "HealthCheckInterval",
    "MaxConsecutiveFailures",
    "TimeoutSeconds",
    # Value Objects - Tool Arguments
    "ToolArguments",
    # Exceptions - Base
    "MCPError",
    # Exceptions - Provider
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderStartError",
    "ProviderDegradedError",
    "CannotStartProviderError",
    "ProviderNotReadyError",
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
    "IProviderRepository",
    "InMemoryProviderRepository",
]
