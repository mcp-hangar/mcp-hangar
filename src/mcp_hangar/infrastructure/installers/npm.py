"""npm package installer using npx.

This module provides installation of npm packages using npx for MCP servers.
"""

import shutil

from ...domain.contracts.installer import InstalledPackage, IPackageInstaller
from ...domain.contracts.registry import PackageInfo
from ...domain.exceptions import RuntimeNotAvailableError
from ...domain.value_objects import ProviderMode
from ...logging_config import get_logger

logger = get_logger(__name__)


class NpmInstaller(IPackageInstaller):
    """Installer for npm packages using npx.

    Uses npx to run npm packages without global installation.
    The package is cached by npm automatically.
    """

    @property
    def registry_type(self) -> str:
        """The registry type this installer handles."""
        return "npm"

    def supports(self, registry_type: str) -> bool:
        """Check if this installer supports the given registry type."""
        return registry_type == self.registry_type

    async def is_runtime_available(self) -> bool:
        """Check if npx is available."""
        return shutil.which("npx") is not None

    async def install(self, package: PackageInfo) -> InstalledPackage:
        """Install an npm package using npx.

        Args:
            package: Package information from the registry.

        Returns:
            Information about the installed package.

        Raises:
            RuntimeNotAvailableError: If npx is not available.
        """
        if not await self.is_runtime_available():
            raise RuntimeNotAvailableError(
                runtime="npx",
                suggestion="Install Node.js from https://nodejs.org/",
            )

        identifier = package.identifier
        if package.version:
            identifier = f"{identifier}@{package.version}"

        command = ["npx", "--yes", identifier]

        if package.transport.args:
            command.extend(package.transport.args)

        logger.info(
            "npm_package_prepared",
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
        """Uninstall an npm package.

        For npx packages, this is a no-op as packages are not globally installed.
        npm manages the cache automatically.
        """
        logger.debug(
            "npm_package_cleanup",
            package=installed.package_info.identifier,
        )
