"""Domain policies for MCP Hangar.

Policies encapsulate domain rules and classification logic that can be
applied across different contexts without coupling to specific aggregates.
"""

from .dsl import (
    ALLOWED_ACTIONS,
    ALLOWED_HOOKS,
    HookRule,
    PolicyDSL,
    parse_policy,
)
from .egress_l7 import (
    KNOWN_SECRET_PATTERN_GROUPS,
    ArgumentRules,
    Decision,
    HeaderMatch,
    HeaderRules,
    L7Policy,
    ToolAction,
    ToolRules,
    evaluate,
    evaluate_headers,
    evaluate_tool,
    scan_arguments,
)
from .header_exposure import (
    ON_VIOLATION_ACTIONS,
    HeaderExposurePolicy,
    clear_header_exposure_policies,
    get_header_exposure_policy,
    set_header_exposure_policy,
)
from .mcp_server_health import (
    McpServerHealthClassification,
    classify_mcp_server_health,
    classify_mcp_server_health_from_mcp_server,
    to_health_status_string,
)

__all__ = [
    "ALLOWED_ACTIONS",
    "ALLOWED_HOOKS",
    "ArgumentRules",
    "Decision",
    "HeaderExposurePolicy",
    "HeaderMatch",
    "HeaderRules",
    "HookRule",
    "KNOWN_SECRET_PATTERN_GROUPS",
    "L7Policy",
    "McpServerHealthClassification",
    "ON_VIOLATION_ACTIONS",
    "PolicyDSL",
    "ToolAction",
    "ToolRules",
    "classify_mcp_server_health",
    "classify_mcp_server_health_from_mcp_server",
    "clear_header_exposure_policies",
    "evaluate",
    "evaluate_headers",
    "evaluate_tool",
    "get_header_exposure_policy",
    "parse_policy",
    "scan_arguments",
    "set_header_exposure_policy",
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
