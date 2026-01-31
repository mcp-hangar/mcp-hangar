"""CLI services - extracted functionality from commands."""

from .claude_desktop import ClaudeDesktopManager
from .config_file import ConfigFileManager
from .provider_registry import (
    get_all_providers,
    get_provider,
    get_providers_by_category,
    PROVIDER_BUNDLES,
    ProviderDefinition,
    search_providers,
)

__all__ = [
    # Provider registry
    "ProviderDefinition",
    "PROVIDER_BUNDLES",
    "get_all_providers",
    "get_provider",
    "get_providers_by_category",
    "search_providers",
    # Config file management
    "ConfigFileManager",
    # Claude Desktop management
    "ClaudeDesktopManager",
]
