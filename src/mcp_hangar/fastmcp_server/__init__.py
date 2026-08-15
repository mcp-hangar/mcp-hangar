"""MCP HTTP Server using FastMCP.

Provides MCP-over-HTTP with proper dependency injection.
No global state - all dependencies passed via constructor.

Endpoints (HTTP mode):
- /health/live   : liveness probe (is the process alive?)
- /health/ready  : readiness probe (can handle traffic?)
- /health/startup: startup probe (is initialization complete?)
- /metrics       : prometheus metrics
- /mcp           : MCP streamable HTTP endpoint

Usage:
    # Recommended: Use MCPServerFactory
    from mcp_hangar.fastmcp_server import MCPServerFactory, HangarFunctions

    hangar = HangarFunctions(
        list=my_list_fn,
        start=my_start_fn,
        stop=my_stop_fn,
        invoke=my_invoke_fn,
        tools=my_tools_fn,
        details=my_details_fn,
        health=my_health_fn,
    )

    factory = MCPServerFactory(hangar)
    mcp = factory.create_server()
"""

from .config import HangarFunctions, ServerConfig
from .factory import MCPServerFactory
from .protocols import (
    HangarApproveFn,
    HangarDetailsFn,
    HangarDiscoveredFn,
    HangarDiscoverFn,
    HangarHealthFn,
    HangarInvokeFn,
    HangarListFn,
    HangarMetricsFn,
    HangarQuarantineFn,
    HangarSourcesFn,
    HangarStartFn,
    HangarStopFn,
    HangarToolsFn,
)

__all__ = [
    # Factory API
    "MCPServerFactory",
    "HangarFunctions",
    "ServerConfig",
    # Protocols
    "HangarListFn",
    "HangarStartFn",
    "HangarStopFn",
    "HangarInvokeFn",
    "HangarToolsFn",
    "HangarDetailsFn",
    "HangarHealthFn",
    "HangarDiscoverFn",
    "HangarDiscoveredFn",
    "HangarQuarantineFn",
    "HangarApproveFn",
    "HangarSourcesFn",
    "HangarMetricsFn",
]
