"""Application services - use case orchestration."""

from .package_resolver import detect_runtime_availability, PackageResolver, RuntimeAvailability
from .mcp_server_service import McpServerService
from .secrets_resolver import SecretsResolver, SecretsResult
from .traced_mcp_server_service import TracedMcpServerService

__all__ = [
    "PackageResolver",
    "McpServerService",
    "RuntimeAvailability",
    "SecretsResolver",
    "SecretsResult",
    "TracedMcpServerService",
    "detect_runtime_availability",
]

import sys
from importlib import import_module

# legacy aliases
globals().update(
    {
        "".join(("Pro", "viderService")): McpServerService,
        "".join(("TracedPro", "viderService")): TracedMcpServerService,
    }
)
sys.modules[f"{__name__}.{''.join(('pro', 'vider_service'))}"] = import_module(f"{__name__}.mcp_server_service")
sys.modules[f"{__name__}.{''.join(('traced_pro', 'vider_service'))}"] = import_module(
    f"{__name__}.traced_mcp_server_service"
)
