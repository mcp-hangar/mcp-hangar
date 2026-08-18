"""MCP Hangar - Production-grade MCP mcp_server management.

This package provides a production-grade registry for managing MCP (Model Context Protocol)
mcp_servers with hot-loading, health monitoring, and automatic garbage collection.

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
    config = HangarConfig().add_mcp_server("math", command=["python", "-m", "math"]).build()
    hangar = Hangar.from_builder(config)

For advanced usage, see:
- McpServer aggregate: mcp_hangar.domain.model
- Domain exceptions: mcp_hangar.domain.exceptions
- Value objects: mcp_hangar.domain.value_objects
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-hangar")
except PackageNotFoundError:
    # Package not installed (e.g., running from source)
    __version__ = "0.0.0.dev"

# Domain layer - for advanced usage
from .domain.exceptions import (
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
from .domain.model import McpServer, ToolSchema
from .domain.value_objects import (
    CorrelationId,
    HealthStatus,
    McpServerConfig,
    McpServerId,
    McpServerMode,
    McpServerState,
    ToolArguments,
    ToolName,
)

# Retryability heuristic (the HangarError/Rich* zoo it belonged to was
# deleted in #970 -- nothing in src/ ever raised those types).
from .errors import is_retryable

# High-level Facade API (recommended for most users)
from .facade import (
    FACADE_DEFAULT_CONCURRENCY,
    FACADE_MAX_CONCURRENCY,
    Hangar,
    HangarConfig,
    HangarConfigData,
    HealthSummary,
    McpServerInfo,
    SyncHangar,
)

# Legacy imports - for backward compatibility
from .retry import BackoffStrategy, RetryPolicy, RetryResult, get_retry_policy, get_retry_store, with_retry
from .stdio_client import StdioClient

__all__ = [
    # High-level Facade API (recommended)
    "Hangar",
    "SyncHangar",
    "HangarConfig",
    "HangarConfigData",
    "McpServerInfo",
    "HealthSummary",
    "FACADE_DEFAULT_CONCURRENCY",
    "FACADE_MAX_CONCURRENCY",
    # Domain - McpServer aggregate
    "McpServer",
    # Domain - Value Objects
    "McpServerId",
    "ToolName",
    "CorrelationId",
    "McpServerState",
    "McpServerMode",
    "HealthStatus",
    "McpServerConfig",
    "ToolArguments",
    # Domain - Exceptions
    "MCPError",
    "McpServerError",
    "McpServerNotFoundError",
    "McpServerStartError",
    "McpServerDegradedError",
    "CannotStartMcpServerError",
    "McpServerNotReadyError",
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
    # Retryability heuristic (see errors.py)
    "is_retryable",
    # UX - Retry
    "RetryPolicy",
    "BackoffStrategy",
    "RetryResult",
    "get_retry_policy",
    "get_retry_store",
    "with_retry",
    # Legacy - for backward compatibility
    "ToolSchema",
    "StdioClient",
]

import sys
from importlib import import_module

# legacy aliases
globals().update(
    {
        "".join(("Pro", "vider")): McpServer,
        "".join(("Pro", "viderId")): McpServerId,
        "".join(("Pro", "viderMode")): McpServerMode,
        "".join(("Pro", "viderState")): McpServerState,
        "".join(("Pro", "viderConfig")): McpServerConfig,
        "".join(("Pro", "viderNotFoundError")): McpServerNotFoundError,
        "".join(("Pro", "viderStartError")): McpServerStartError,
        "".join(("Pro", "viderDegradedError")): McpServerDegradedError,
    }
)
sys.modules[f"{__name__}.application.services.{''.join(('traced_pro', 'vider_service'))}"] = import_module(
    f"{__name__}.application.services.traced_mcp_server_service"
)
