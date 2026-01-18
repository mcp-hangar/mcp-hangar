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
    ConfigurationError as HangarConfigurationError,
)
from .errors import (
    HangarError,
    NetworkError,
    ProviderCrashError,
    ProviderProtocolError,
    RateLimitError,
    TransientError,
    is_retryable,
    map_exception_to_hangar_error,
)
from .errors import (
    ProviderDegradedError as HangarProviderDegradedError,
)
from .errors import (
    ProviderNotFoundError as HangarProviderNotFoundError,
)
from .errors import (
    TimeoutError as HangarTimeoutError,
)
from .errors import (
    ToolNotFoundError as HangarToolNotFoundError,
)

# Legacy imports - for backward compatibility (re-exports from domain)
from .models import ToolSchema
from .progress import (
    ProgressCallback,
    ProgressEvent,
    ProgressStage,
    ProgressTracker,
    create_progress_tracker,
    get_stage_message,
)
from .retry import BackoffStrategy, RetryPolicy, RetryResult, get_retry_policy, get_retry_store, with_retry
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
    "ToolSchema",
    "StdioClient",
]
