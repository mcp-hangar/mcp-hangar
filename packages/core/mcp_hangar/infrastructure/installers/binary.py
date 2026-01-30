"""Binary installer for pre-built MCP server binaries.

This module provides installation of pre-built binaries with SHA256 verification.
"""

import hashlib
import os
from pathlib import Path
import stat

import httpx

from ...domain.contracts.installer import InstalledPackage, IPackageInstaller
from ...domain.contracts.registry import PackageInfo
from ...domain.exceptions import PackageInstallationError, PackageVerificationError, RuntimeNotAvailableError
from ...domain.value_objects import ProviderMode
from ...logging_config import get_logger

logger = get_logger(__name__)


class BinaryInstaller(IPackageInstaller):
    """Installer for pre-built MCP server binaries.

    Downloads binaries to a local directory and verifies SHA256 checksums.
    Binaries are stored in ~/.local/share/mcp-hangar/bin/ by default.
    """

    DEFAULT_BIN_DIR = Path.home() / ".local" / "share" / "mcp-hangar" / "bin"
    DOWNLOAD_TIMEOUT = 60.0

    def __init__(self, bin_dir: Path | None = None):
        """Initialize the binary installer.

        Args:
            bin_dir: Optional custom binary directory.
        """
        self._bin_dir = bin_dir or self.DEFAULT_BIN_DIR

    @property
    def registry_type(self) -> str:
        """The registry type this installer handles."""
        return "mcpb"

    @property
    def bin_dir(self) -> Path:
        """Get the binary directory."""
        return self._bin_dir

    def supports(self, registry_type: str) -> bool:
        """Check if this installer supports the given registry type."""
        return registry_type == self.registry_type

    async def is_runtime_available(self) -> bool:
        """Check if binary execution is available.

        Binary installation is always available as long as we can write
        to the bin directory.
        """
        try:
            self._bin_dir.mkdir(parents=True, exist_ok=True)
            test_file = self._bin_dir / ".write_test"
            test_file.touch()
            test_file.unlink()
            return True
        except (OSError, PermissionError):
            return False

    async def install(self, package: PackageInfo) -> InstalledPackage:
        """Download and install a binary package.

        Args:
            package: Package information from the registry.

        Returns:
            Information about the installed package.

        Raises:
            RuntimeNotAvailableError: If binary installation is not available.
            PackageInstallationError: If download or installation fails.
            PackageVerificationError: If SHA256 verification fails.
        """
        if not await self.is_runtime_available():
            raise RuntimeNotAvailableError(
                runtime="binary",
                suggestion=f"Ensure write access to {self._bin_dir}",
            )

        url = package.identifier
        binary_name = self._extract_binary_name(url, package.version)
        binary_path = self._bin_dir / binary_name

        logger.info(
            "binary_downloading",
            url=url,
            destination=str(binary_path),
        )

        try:
            self._bin_dir.mkdir(parents=True, exist_ok=True)

            content = await self._download(url)

            if package.file_sha256:
                actual_hash = hashlib.sha256(content).hexdigest()
                if actual_hash.lower() != package.file_sha256.lower():
                    raise PackageVerificationError(
                        package=url,
                        expected_hash=package.file_sha256,
                        actual_hash=actual_hash,
                    )
                logger.debug(
                    "binary_verified",
                    url=url,
                    sha256=actual_hash[:16] + "...",
                )

            binary_path.write_bytes(content)

            current_mode = binary_path.stat().st_mode
            binary_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        except PackageVerificationError:
            raise
        except httpx.HTTPError as e:
            raise PackageInstallationError(
                package=url,
                reason=f"Download failed: {e}",
            )
        except OSError as e:
            raise PackageInstallationError(
                package=url,
                reason=f"Failed to write binary: {e}",
            )

        command = [str(binary_path)]

        if package.transport.args:
            command.extend(package.transport.args)

        logger.info(
            "binary_installed",
            path=str(binary_path),
            size=len(content),
        )

        def cleanup() -> None:
            """Remove the installed binary."""
            try:
                if binary_path.exists():
                    binary_path.unlink()
                    logger.debug("binary_removed", path=str(binary_path))
            except OSError as e:
                logger.warning("binary_removal_failed", path=str(binary_path), error=str(e))

        return InstalledPackage(
            package_info=package,
            install_path=binary_path,
            command=command,
            mode=ProviderMode.SUBPROCESS,
            cleanup=cleanup,
        )

    async def uninstall(self, installed: InstalledPackage) -> None:
        """Remove an installed binary.

        Args:
            installed: Previously installed package information.
        """
        if installed.cleanup:
            installed.cleanup()

    async def _download(self, url: str) -> bytes:
        """Download a file from URL.

        Args:
            url: URL to download from.

        Returns:
            Downloaded file content.

        Raises:
            httpx.HTTPError: If download fails.
        """
        async with httpx.AsyncClient(timeout=self.DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    def _extract_binary_name(self, url: str, version: str | None) -> str:
        """Extract binary name from URL.

        Args:
            url: Download URL.
            version: Optional version string.

        Returns:
            Binary filename.
        """
        from urllib.parse import urlparse

        path = urlparse(url).path
        filename = os.path.basename(path)

        if not filename or filename == "/":
            hash_part = hashlib.sha256(url.encode()).hexdigest()[:8]
            filename = f"mcp-server-{hash_part}"
            if version:
                filename = f"{filename}-{version}"

        return filename
