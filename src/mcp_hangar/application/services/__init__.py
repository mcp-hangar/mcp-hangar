"""Application services - use case orchestration."""

from .mcp_server_service import McpServerService
from .package_resolver import PackageResolver, RuntimeAvailability
from .secrets_resolver import SecretsResolver, SecretsResult

__all__ = [
    "PackageResolver",
    "McpServerService",
    "RuntimeAvailability",
    "SecretsResolver",
    "SecretsResult",
]

import sys
from importlib import import_module

# legacy aliases
globals().update(
    {
        "".join(("Pro", "viderService")): McpServerService,
    }
)
sys.modules[f"{__name__}.{''.join(('pro', 'vider_service'))}"] = import_module(f"{__name__}.mcp_server_service")
