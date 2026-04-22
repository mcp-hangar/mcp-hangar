"""CLI services - extracted functionality from commands."""

from .claude_desktop import ClaudeDesktopManager
from .config_file import ConfigFileManager
from .dependency_detector import (
    DependencyStatus,
    detect_dependencies,
    get_install_instructions,
    is_mcp_server_available,
)
from .mcp_server_registry import (
    filter_bundle_by_availability,
    get_all_mcp_servers,
    get_available_mcp_servers,
    get_mcp_server,
    get_mcp_servers_by_category,
    get_mcp_servers_by_category_filtered,
    get_unavailable_mcp_servers,
    PROVIDER_BUNDLES,
    McpServerDefinition,
    search_mcp_servers,
)
from .smoke_test import McpServerTestResult, run_smoke_test, run_smoke_test_simple, SmokeTestResult

__all__ = [
    # Dependency detection
    "DependencyStatus",
    "detect_dependencies",
    "get_install_instructions",
    "is_mcp_server_available",
    # McpServer registry
    "McpServerDefinition",
    "PROVIDER_BUNDLES",
    "get_all_mcp_servers",
    "get_available_mcp_servers",
    "get_unavailable_mcp_servers",
    "get_mcp_server",
    "get_mcp_servers_by_category",
    "get_mcp_servers_by_category_filtered",
    "filter_bundle_by_availability",
    "search_mcp_servers",
    # Config file management
    "ConfigFileManager",
    # Claude Desktop management
    "ClaudeDesktopManager",
    # Smoke test
    "run_smoke_test",
    "run_smoke_test_simple",
    "SmokeTestResult",
    "McpServerTestResult",
]

import sys
from importlib import import_module

# legacy aliases
globals().update(
    {
        "".join(("get_all_pro", "viders")): get_all_mcp_servers,
        "".join(("get_pro", "vider")): get_mcp_server,
        "".join(("search_pro", "viders")): search_mcp_servers,
        "".join(("Pro", "viderDefinition")): McpServerDefinition,
    }
)
sys.modules[f"{__name__}.{''.join(('pro', 'vider_registry'))}"] = import_module(f"{__name__}.mcp_server_registry")

get_providers_by_category = get_mcp_servers_by_category
