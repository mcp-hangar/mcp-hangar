"""OCI/Docker container installer.

This module provides installation and management of OCI container images
for running MCP servers in containers.
"""

import asyncio
import shutil

from ...domain.contracts.installer import InstalledPackage, IPackageInstaller
from ...domain.contracts.registry import PackageInfo
from ...domain.exceptions import PackageInstallationError, RuntimeNotAvailableError
from ...domain.value_objects import ProviderMode
from ...logging_config import get_logger

logger = get_logger(__name__)


class OciInstaller(IPackageInstaller):
    """Installer for OCI container images.

    Supports both Docker and Podman as container runtimes.
    Prefers Podman if available, falls back to Docker.
    """

    def __init__(self):
        """Initialize the OCI installer."""
        self._runtime: str | None = None

    @property
    def registry_type(self) -> str:
        """The registry type this installer handles."""
        return "oci"

    def supports(self, registry_type: str) -> bool:
        """Check if this installer supports the given registry type."""
        return registry_type == self.registry_type

    async def is_runtime_available(self) -> bool:
        """Check if Docker or Podman is available."""
        runtime = await self._detect_runtime()
        return runtime is not None

    async def _detect_runtime(self) -> str | None:
        """Detect available container runtime.

        Returns:
            Runtime name ("podman" or "docker") or None if not available.
        """
        if self._runtime is not None:
            return self._runtime

        if shutil.which("podman"):
            self._runtime = "podman"
            return self._runtime

        if shutil.which("docker"):
            self._runtime = "docker"
            return self._runtime

        return None

    async def install(self, package: PackageInfo) -> InstalledPackage:
        """Pull an OCI container image.

        Args:
            package: Package information from the registry.

        Returns:
            Information about the installed package.

        Raises:
            RuntimeNotAvailableError: If no container runtime is available.
            PackageInstallationError: If pull fails.
        """
        runtime = await self._detect_runtime()
        if runtime is None:
            raise RuntimeNotAvailableError(
                runtime="docker/podman",
                suggestion="Install Docker from https://docs.docker.com/get-docker/ or Podman from https://podman.io/",
            )

        image = package.identifier
        if package.version:
            image = f"{image}:{package.version}"

        logger.info(
            "oci_pulling_image",
            image=image,
            runtime=runtime,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                runtime,
                "pull",
                image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                raise PackageInstallationError(
                    package=image,
                    reason="Failed to pull container image",
                    stderr=stderr.decode("utf-8", errors="replace"),
                    exit_code=proc.returncode,
                )

        except OSError as e:
            raise PackageInstallationError(
                package=image,
                reason=f"Failed to run {runtime}: {e}",
            )

        command = [runtime, "run", "--rm", "-i"]

        if package.transport.args:
            command.extend(package.transport.args)

        command.append(image)

        logger.info(
            "oci_image_ready",
            image=image,
            runtime=runtime,
        )

        def cleanup() -> None:
            """Remove the pulled image."""
            try:
                import subprocess

                subprocess.run(
                    [runtime, "rmi", image],
                    capture_output=True,
                    timeout=30,
                )
                logger.debug("oci_image_removed", image=image)
            except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
                logger.warning("oci_image_removal_failed", image=image, error=str(e))

        return InstalledPackage(
            package_info=package,
            install_path=None,
            command=command,
            mode=ProviderMode.DOCKER,
            cleanup=cleanup,
        )

    async def uninstall(self, installed: InstalledPackage) -> None:
        """Remove an OCI container image.

        Args:
            installed: Previously installed package information.
        """
        if installed.cleanup:
            installed.cleanup()
