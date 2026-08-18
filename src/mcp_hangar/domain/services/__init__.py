"""Domain services - interfaces for infrastructure operations.

The concrete launchers are NOT re-exported here. They were, via a deprecated
shim that warned from v1.0.2 onward and survived the 2.0 major; import them
from ``mcp_hangar.infrastructure.launchers``, which is where they live. What
this package offers for launching is the port, ``IMcpServerLauncher``.
"""

from __future__ import annotations


# Re-export exception from canonical location for convenience
from ..exceptions import McpServerStartError
from ..contracts.launcher import IMcpServerLauncher, LaunchResult, TransportClient
from .error_diagnostics import collect_startup_diagnostics, get_suggestion_for_error
from .image_builder import BuildConfig, get_image_builder, ImageBuilder
from .tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
    ToolAccessResolver,
)


def __getattr__(name: str) -> object:
    if name in {
        "get_tool_projection_registry",
        "reset_tool_projection_registry",
        "ToolProjection",
        "ToolProjectionRegistry",
    }:
        from mcp_hangar.application.read_models.tool_projection import (
            get_tool_projection_registry,
            reset_tool_projection_registry,
            ToolProjection,
            ToolProjectionRegistry,
        )

        return {
            "get_tool_projection_registry": get_tool_projection_registry,
            "reset_tool_projection_registry": reset_tool_projection_registry,
            "ToolProjection": ToolProjection,
            "ToolProjectionRegistry": ToolProjectionRegistry,
        }[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "IMcpServerLauncher",
    "LaunchResult",
    "TransportClient",
    "ImageBuilder",
    "BuildConfig",
    "get_image_builder",
    "McpServerStartError",
    "collect_startup_diagnostics",
    "get_suggestion_for_error",
    "ToolAccessResolver",
    "get_tool_access_resolver",
    "reset_tool_access_resolver",
    "ToolProjection",
    "ToolProjectionRegistry",
    "get_tool_projection_registry",
    "reset_tool_projection_registry",
]
