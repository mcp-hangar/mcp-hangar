"""Package installer contract for MCP server packages.

This module defines the interface for installing MCP server packages from
various registries (npm, PyPI, OCI, mcpb). Implementations are provided
by the infrastructure layer.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..value_objects import ProviderMode
from .registry import PackageInfo


@dataclass
class InstalledPackage:
    """Information about an installed MCP server package.

    Attributes:
        package_info: Original package information from the registry.
        install_path: Path where the package was installed (None for npx/uvx).
        command: Command and arguments to run the MCP server.
        mode: Provider mode to use when running the server.
        env: Environment variables to set when running the server.
        cleanup: Optional cleanup function to uninstall the package.
    """

    package_info: PackageInfo
    install_path: Path | None
    command: list[str]
    mode: ProviderMode
    env: dict[str, str] = field(default_factory=dict)
    cleanup: Callable[[], None] | None = None


class IPackageInstaller(Protocol):
    """Contract for installing MCP server packages.

    Implementations should handle package-specific installation logic,
    including downloading, verification, and setup.
    """

    @property
    def registry_type(self) -> str:
        """The registry type this installer handles (e.g., "npm", "pypi", "oci", "mcpb")."""
        ...

    def supports(self, registry_type: str) -> bool:
        """Check if this installer supports the given registry type.

        Args:
            registry_type: Package registry type ("npm", "pypi", "oci", "mcpb").

        Returns:
            True if this installer can handle the registry type.
        """
        ...

    async def install(self, package: PackageInfo) -> InstalledPackage:
        """Install an MCP server package.

        Args:
            package: Package information from the registry.

        Returns:
            Information about the installed package.

        Raises:
            InstallationError: If the installation fails.
        """
        ...

    async def uninstall(self, installed: InstalledPackage) -> None:
        """Uninstall an MCP server package.

        Args:
            installed: Previously installed package information.

        Raises:
            InstallationError: If the uninstallation fails.
        """
        ...

    async def is_runtime_available(self) -> bool:
        """Check if the required runtime is available.

        For npm installer, checks if npx is available.
        For pypi installer, checks if uvx is available.
        For oci installer, checks if docker/podman is available.

        Returns:
            True if the runtime is available.
        """
        ...
