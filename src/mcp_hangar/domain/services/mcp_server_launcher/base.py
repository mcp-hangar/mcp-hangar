"""Deprecated mcp_server launcher import shim."""

from __future__ import annotations

import warnings

from mcp_hangar.infrastructure.launchers.base import McpServerLauncher

warnings.warn(
    "mcp_hangar.domain.services.mcp_server_launcher.base is deprecated; import from "
    "mcp_hangar.infrastructure.launchers.base instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["McpServerLauncher"]
