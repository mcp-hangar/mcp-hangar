"""CLI services - extracted functionality from commands."""

from .claude_desktop import ClaudeDesktopManager
from .config_file import ConfigFileManager
from .dependency_detector import DependencyStatus, detect_dependencies, get_install_instructions, is_provider_available
from .provider_registry import (
    filter_bundle_by_availability,
    get_all_providers,
    get_available_providers,
    get_provider,
    get_providers_by_category,
    get_providers_by_category_filtered,
    get_unavailable_providers,
    PROVIDER_BUNDLES,
    ProviderDefinition,
    search_providers,
)
from .smoke_test import ProviderTestResult, run_smoke_test, run_smoke_test_simple, SmokeTestResult

__all__ = [
    # Dependency detection
    "DependencyStatus",
    "detect_dependencies",
    "get_install_instructions",
    "is_provider_available",
    # Provider registry
    "ProviderDefinition",
    "PROVIDER_BUNDLES",
    "get_all_providers",
    "get_available_providers",
    "get_unavailable_providers",
    "get_provider",
    "get_providers_by_category",
    "get_providers_by_category_filtered",
    "filter_bundle_by_availability",
    "search_providers",
    # Config file management
    "ConfigFileManager",
    # Claude Desktop management
    "ClaudeDesktopManager",
    # Smoke test
    "run_smoke_test",
    "run_smoke_test_simple",
    "SmokeTestResult",
    "ProviderTestResult",
]
