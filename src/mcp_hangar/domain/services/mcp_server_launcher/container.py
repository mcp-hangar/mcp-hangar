"""Deprecated mcp_server launcher import shim."""

from __future__ import annotations

import warnings

from mcp_hangar.infrastructure.launchers.container import ContainerConfig, ContainerLauncher

warnings.warn(
    "mcp_hangar.domain.services.mcp_server_launcher.container is deprecated; import from "
    "mcp_hangar.infrastructure.launchers.container instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["ContainerConfig", "ContainerLauncher"]
