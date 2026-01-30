"""PyPI package installer using uvx.

This module provides installation of PyPI packages using uvx for MCP servers.
"""

import shutil

from ...domain.contracts.installer import InstalledPackage, IPackageInstaller
from ...domain.contracts.registry import PackageInfo
from ...domain.exceptions import RuntimeNotAvailableError
from ...domain.value_objects import ProviderMode
from ...logging_config import get_logger

logger = get_logger(__name__)


class PyPIInstaller(IPackageInstaller):
    """Installer for PyPI packages using uvx.

    Uses uvx (from uv package manager) to run Python packages in isolated
    environments without global installation.
    """

    @property
    def registry_type(self) -> str:
        """The registry type this installer handles."""
        return "pypi"

    def supports(self, registry_type: str) -> bool:
        """Check if this installer supports the given registry type."""
        return registry_type == self.registry_type

    async def is_runtime_available(self) -> bool:
        """Check if uvx is available."""
        return shutil.which("uvx") is not None

    async def install(self, package: PackageInfo) -> InstalledPackage:
        """Install a PyPI package using uvx.

        Args:
            package: Package information from the registry.

        Returns:
            Information about the installed package.

        Raises:
            RuntimeNotAvailableError: If uvx is not available.
        """
        if not await self.is_runtime_available():
            raise RuntimeNotAvailableError(
                runtime="uvx",
                suggestion="Install uv from https://docs.astral.sh/uv/",
            )

        identifier = package.identifier
        if package.version:
            identifier = f"{identifier}=={package.version}"

        command = ["uvx", identifier]

        if package.transport.args:
            command.extend(package.transport.args)

        logger.info(
            "pypi_package_prepared",
            package=identifier,
            command=command,
        )

        return InstalledPackage(
            package_info=package,
            install_path=None,
            command=command,
            mode=ProviderMode.SUBPROCESS,
            cleanup=None,
        )

    async def uninstall(self, installed: InstalledPackage) -> None:
        """Uninstall a PyPI package.

        For uvx packages, this is a no-op as packages run in isolated environments.
        uv manages the cache automatically.
        """
        logger.debug(
            "pypi_package_cleanup",
            package=installed.package_info.identifier,
        )
