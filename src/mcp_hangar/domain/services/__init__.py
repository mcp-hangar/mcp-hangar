"""Domain services - interfaces for infrastructure operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

# Re-export exception from canonical location for convenience
from ..exceptions import McpServerStartError
from ..contracts.launcher import IMcpServerLauncher, LaunchResult
from .audit_service import AuditService
from .error_diagnostics import collect_startup_diagnostics, get_suggestion_for_error
from .image_builder import BuildConfig, get_image_builder, ImageBuilder
from .tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
    ToolAccessResolver,
)

if TYPE_CHECKING:
    from mcp_hangar.infrastructure.launchers import (
        ContainerConfig,
        ContainerLauncher,
        DockerLauncher,
        HttpLauncher,
        McpServerLauncher,
        SubprocessLauncher,
        get_launcher,
    )


def __getattr__(name: str) -> object:
    if name in {
        "ContainerConfig",
        "ContainerLauncher",
        "DockerLauncher",
        "get_launcher",
        "HttpLauncher",
        "McpServerLauncher",
        "SubprocessLauncher",
    }:
        from . import mcp_server_launcher as launcher_module

        return getattr(launcher_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AuditService",
    "IMcpServerLauncher",
    "LaunchResult",
    "McpServerLauncher",
    "SubprocessLauncher",
    "DockerLauncher",
    "ContainerLauncher",
    "ContainerConfig",
    "HttpLauncher",
    "get_launcher",
    "ImageBuilder",
    "BuildConfig",
    "get_image_builder",
    "McpServerStartError",
    "collect_startup_diagnostics",
    "get_suggestion_for_error",
    "ToolAccessResolver",
    "get_tool_access_resolver",
    "reset_tool_access_resolver",
]
