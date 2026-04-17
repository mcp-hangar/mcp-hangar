"""Deprecated provider launcher import shim."""

from __future__ import annotations

import warnings

from mcp_hangar.infrastructure.launchers.subprocess import SubprocessLauncher

warnings.warn(
    "mcp_hangar.domain.services.provider_launcher.subprocess is deprecated; import from "
    "mcp_hangar.infrastructure.launchers.subprocess instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["SubprocessLauncher"]
