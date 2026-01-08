"""MCP Registry - Hot-load provider management.

This package provides a production-grade registry for managing MCP (Model Context Protocol)
providers with hot-loading, health monitoring, and automatic garbage collection.

New code should use:
- Provider aggregate from mcp_hangar.domain.model
- Domain exceptions from mcp_hangar.domain.exceptions
- Value objects from mcp_hangar.domain.value_objects

Legacy imports are maintained for backward compatibility.
"""

# Domain layer - preferred imports for new code
from .domain.exceptions import (
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
from .domain.model import Provider
from .domain.value_objects import (
    CorrelationId,
    HealthStatus,
    ProviderConfig,
    ProviderId,
    ProviderMode,
    ProviderState,
    ToolArguments,
    ToolName,
)

# UX Improvements - Rich errors, retry, progress
from .errors import (
    HangarError,
    TransientError,
    ProviderProtocolError,
    ProviderCrashError,
    NetworkError,
    ConfigurationError as HangarConfigurationError,
    ProviderNotFoundError as HangarProviderNotFoundError,
    ToolNotFoundError as HangarToolNotFoundError,
    TimeoutError as HangarTimeoutError,
    RateLimitError,
    ProviderDegradedError as HangarProviderDegradedError,
    map_exception_to_hangar_error,
    is_retryable,
)
from .retry import (
    RetryPolicy,
    BackoffStrategy,
    RetryResult,
    get_retry_policy,
    get_retry_store,
    with_retry,
)
from .progress import (
    ProgressStage,
    ProgressEvent,
    ProgressTracker,
    ProgressCallback,
    create_progress_tracker,
    get_stage_message,
)

# Legacy imports - for backward compatibility
from .models import (
    InvocationContext,
    ProviderConnection,
    ProviderHealth,
    ProviderSpec,
    ToolSchema,
)
from .provider_manager import ProviderManager
from .stdio_client import StdioClient

__all__ = [
    # Domain - Provider aggregate (preferred)
    "Provider",
    # Domain - Value Objects
    "ProviderId",
    "ToolName",
    "CorrelationId",
    "ProviderState",
    "ProviderMode",
    "HealthStatus",
    "ProviderConfig",
    "ToolArguments",
    # Domain - Exceptions
    "MCPError",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderStartError",
    "ProviderDegradedError",
    "CannotStartProviderError",
    "ProviderNotReadyError",
    "InvalidStateTransitionError",
    "ToolError",
    "ToolNotFoundError",
    "ToolInvocationError",
    "ToolTimeoutError",
    "ClientError",
    "ClientNotConnectedError",
    "ClientTimeoutError",
    "ValidationError",
    "ConfigurationError",
    "RateLimitExceeded",
    # UX - Rich Errors
    "HangarError",
    "TransientError",
    "ProviderProtocolError",
    "ProviderCrashError",
    "NetworkError",
    "HangarConfigurationError",
    "HangarProviderNotFoundError",
    "HangarToolNotFoundError",
    "HangarTimeoutError",
    "RateLimitError",
    "HangarProviderDegradedError",
    "map_exception_to_hangar_error",
    "is_retryable",
    # UX - Retry
    "RetryPolicy",
    "BackoffStrategy",
    "RetryResult",
    "get_retry_policy",
    "get_retry_store",
    "with_retry",
    # UX - Progress
    "ProgressStage",
    "ProgressEvent",
    "ProgressTracker",
    "ProgressCallback",
    "create_progress_tracker",
    "get_stage_message",
    # Legacy - for backward compatibility
    "ProviderSpec",
    "ToolSchema",
    "InvocationContext",
    "ProviderHealth",
    "ProviderConnection",
    "ProviderManager",
    "StdioClient",
]
