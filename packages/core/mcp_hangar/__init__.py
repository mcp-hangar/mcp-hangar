"""MCP Hangar - Production-grade MCP provider management.

This package provides a production-grade registry for managing MCP (Model Context Protocol)
providers with hot-loading, health monitoring, and automatic garbage collection.

Quick Start (recommended):
    from mcp_hangar import Hangar, SyncHangar

    # Async usage
    async with Hangar.from_config("config.yaml") as hangar:
        result = await hangar.invoke("math", "add", {"a": 1, "b": 2})

    # Sync usage
    with SyncHangar.from_config("config.yaml") as hangar:
        result = hangar.invoke("math", "add", {"a": 1, "b": 2})

    # Programmatic configuration
    from mcp_hangar import HangarConfig
    config = HangarConfig().add_provider("math", command=["python", "-m", "math"]).build()
    hangar = Hangar.from_builder(config)

For advanced usage, see:
- Provider aggregate: mcp_hangar.domain.model
- Domain exceptions: mcp_hangar.domain.exceptions
- Value objects: mcp_hangar.domain.value_objects
"""

# Domain layer - for advanced usage
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
    HangarError,
    is_retryable,
    map_exception_to_hangar_error,
    NetworkError,
    ProviderCrashError,
    ProviderDegradedError as HangarProviderDegradedError,
    ProviderNotFoundError as HangarProviderNotFoundError,
    ProviderProtocolError,
    RateLimitError,
    TimeoutError as HangarTimeoutError,
    ToolNotFoundError as HangarToolNotFoundError,
    TransientError,
)

# High-level Facade API (recommended for most users)
from .facade import Hangar, HangarConfig, HangarConfigData, HealthSummary, ProviderInfo, SyncHangar

# Legacy imports - for backward compatibility (re-exports from domain)
from .models import ToolSchema
from .progress import (
    create_progress_tracker,
    get_stage_message,
    ProgressCallback,
    ProgressEvent,
    ProgressStage,
    ProgressTracker,
)
from .retry import BackoffStrategy, get_retry_policy, get_retry_store, RetryPolicy, RetryResult, with_retry
from .stdio_client import StdioClient

__all__ = [
    # High-level Facade API (recommended)
    "Hangar",
    "SyncHangar",
    "HangarConfig",
    "HangarConfigData",
    "ProviderInfo",
    "HealthSummary",
    # Domain - Provider aggregate
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
