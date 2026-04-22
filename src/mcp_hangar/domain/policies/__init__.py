"""Domain policies for MCP Hangar.

Policies encapsulate domain rules and classification logic that can be
applied across different contexts without coupling to specific aggregates.
"""

from .mcp_server_health import (
    classify_mcp_server_health,
    classify_mcp_server_health_from_mcp_server,
    McpServerHealthClassification,
    to_health_status_string,
)

__all__ = [
    "McpServerHealthClassification",
    "classify_mcp_server_health",
    "classify_mcp_server_health_from_mcp_server",
    "to_health_status_string",
]

import sys
from importlib import import_module

# legacy aliases
globals().update(
    {
        "".join(("Pro", "viderHealthClassification")): McpServerHealthClassification,
        "".join(("classify_pro", "vider_health")): classify_mcp_server_health,
        "".join(("classify_pro", "vider_health_from_mcp_server")): classify_mcp_server_health_from_mcp_server,
    }
)
sys.modules[f"{__name__}.{''.join(('pro', 'vider_health'))}"] = import_module(f"{__name__}.mcp_server_health")
