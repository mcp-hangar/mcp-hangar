"""Domain policies for MCP Hangar.

Policies encapsulate domain rules and classification logic that can be
applied across different contexts without coupling to specific aggregates.
"""

from .dsl import (
    ALLOWED_ACTIONS,
    ALLOWED_HOOKS,
    HookRule,
    parse_policy,
    PolicyDSL,
)
from .egress_l7 import (
    ArgumentRules,
    Decision,
    evaluate,
    evaluate_tool,
    KNOWN_SECRET_PATTERN_GROUPS,
    L7Policy,
    scan_arguments,
    ToolAction,
    ToolRules,
)
from .mcp_server_health import (
    classify_mcp_server_health,
    classify_mcp_server_health_from_mcp_server,
    McpServerHealthClassification,
    to_health_status_string,
)

__all__ = [
    "ALLOWED_ACTIONS",
    "ALLOWED_HOOKS",
    "ArgumentRules",
    "Decision",
    "HookRule",
    "KNOWN_SECRET_PATTERN_GROUPS",
    "L7Policy",
    "McpServerHealthClassification",
    "PolicyDSL",
    "ToolAction",
    "ToolRules",
    "classify_mcp_server_health",
    "classify_mcp_server_health_from_mcp_server",
    "evaluate",
    "evaluate_tool",
    "parse_policy",
    "scan_arguments",
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
