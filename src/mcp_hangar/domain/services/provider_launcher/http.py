"""Deprecated provider launcher import shim."""

from __future__ import annotations

import warnings

from mcp_hangar.infrastructure.launchers.http import HttpLauncher

warnings.warn(
    "mcp_hangar.domain.services.provider_launcher.http is deprecated; import from "
    "mcp_hangar.infrastructure.launchers.http instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["HttpLauncher"]
