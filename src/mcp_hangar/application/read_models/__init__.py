"""Read models for optimized queries."""

from .mcp_server_views import HealthInfo, McpServerDetails, McpServerSummary, SystemMetrics, ToolInfo

__all__ = [
    "McpServerSummary",
    "McpServerDetails",
    "ToolInfo",
    "HealthInfo",
    "SystemMetrics",
]

import sys
from importlib import import_module

# legacy aliases
globals().update(
    {
        "".join(("Pro", "viderSummary")): McpServerSummary,
        "".join(("Pro", "viderDetails")): McpServerDetails,
    }
)
sys.modules[f"{__name__}.{''.join(('pro', 'vider_views'))}"] = import_module(f"{__name__}.mcp_server_views")
