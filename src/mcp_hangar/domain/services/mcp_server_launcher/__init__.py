"""Deprecated mcp_server launcher import shim."""

from __future__ import annotations

import warnings

from mcp_hangar.infrastructure.launchers import (
    ContainerConfig,
    ContainerLauncher,
    DockerLauncher,
    get_launcher,
    HttpLauncher,
    McpServerLauncher,
    SubprocessLauncher,
)

warnings.warn(
    "mcp_hangar.domain.services.mcp_server_launcher is deprecated; import from "
    "mcp_hangar.infrastructure.launchers instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    # Base
    "McpServerLauncher",
    # Implementations
    "SubprocessLauncher",
    "DockerLauncher",
    "ContainerLauncher",
    "ContainerConfig",
    "HttpLauncher",
    # Factory
    "get_launcher",
]
