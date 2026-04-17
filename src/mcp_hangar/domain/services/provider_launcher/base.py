"""Deprecated provider launcher import shim."""

from __future__ import annotations

import warnings

from mcp_hangar.infrastructure.launchers.base import ProviderLauncher

warnings.warn(
    "mcp_hangar.domain.services.provider_launcher.base is deprecated; import from "
    "mcp_hangar.infrastructure.launchers.base instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["ProviderLauncher"]
