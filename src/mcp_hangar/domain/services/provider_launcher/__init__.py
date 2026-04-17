"""Deprecated provider launcher import shim."""

from __future__ import annotations

import warnings

from mcp_hangar.infrastructure.launchers import (
    ContainerConfig,
    ContainerLauncher,
    DockerLauncher,
    get_launcher,
    HttpLauncher,
    ProviderLauncher,
    SubprocessLauncher,
)

warnings.warn(
    "mcp_hangar.domain.services.provider_launcher is deprecated; import from "
    "mcp_hangar.infrastructure.launchers instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    # Base
    "ProviderLauncher",
    # Implementations
    "SubprocessLauncher",
    "DockerLauncher",
    "ContainerLauncher",
    "ContainerConfig",
    "HttpLauncher",
    # Factory
    "get_launcher",
]
