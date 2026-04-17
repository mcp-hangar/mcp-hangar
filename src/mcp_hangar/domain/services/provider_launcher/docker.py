"""Deprecated provider launcher import shim."""

from __future__ import annotations

import warnings

from mcp_hangar.infrastructure.launchers.docker import DockerLauncher

warnings.warn(
    "mcp_hangar.domain.services.provider_launcher.docker is deprecated; import from "
    "mcp_hangar.infrastructure.launchers.docker instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["DockerLauncher"]
