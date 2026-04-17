"""Deprecated provider launcher import shim."""

from __future__ import annotations

import warnings

from mcp_hangar.infrastructure.launchers.factory import get_launcher

warnings.warn(
    "mcp_hangar.domain.services.provider_launcher.factory is deprecated; import from "
    "mcp_hangar.infrastructure.launchers.factory instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["get_launcher"]
