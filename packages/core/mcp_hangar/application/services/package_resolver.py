"""Package resolver for selecting the best package to install.

This module provides functionality to select the best package distribution
from multiple available options based on runtime availability and preferences.
"""

from dataclasses import dataclass
from typing import Protocol

from ...domain.contracts.registry import PackageInfo
from ...logging_config import get_logger

logger = get_logger(__name__)


class IRuntimeChecker(Protocol):
    """Protocol for checking runtime availability."""

    async def is_available(self, registry_type: str) -> bool:
        """Check if a runtime for the given registry type is available."""
        ...


@dataclass
class RuntimeAvailability:
    """Runtime availability status.

    Attributes:
        pypi: Whether uvx is available.
        npm: Whether npx is available.
        oci: Whether docker/podman is available.
        binary: Whether binary execution is available.
    """

    pypi: bool = False
    npm: bool = False
    oci: bool = False
    binary: bool = True


class PackageResolver:
    """Resolves the best package to install from available options.

    Selects packages based on:
    1. User preference (if specified)
    2. Runtime availability
    3. Priority order: pypi (uvx) > npm (npx) > oci (docker) > binary (mcpb)

    The priority favors lighter-weight runtimes over heavier ones.
    """

    PRIORITY_ORDER = ["pypi", "npm", "oci", "mcpb"]

    def __init__(self, runtime_availability: RuntimeAvailability | None = None):
        """Initialize the package resolver.

        Args:
            runtime_availability: Pre-computed runtime availability.
                                  If None, all runtimes are assumed available.
        """
        self._availability = runtime_availability or RuntimeAvailability(pypi=True, npm=True, oci=True, binary=True)

    def resolve(
        self,
        packages: list[PackageInfo],
        preference: str | None = None,
    ) -> PackageInfo | None:
        """Select the best package from available options.

        Args:
            packages: List of available package distributions.
            preference: Optional preferred registry type ("npm", "pypi", "oci", "mcpb").

        Returns:
            The best package to install, or None if no suitable package found.
        """
        if not packages:
            return None

        available = self._filter_available(packages)
        if not available:
            logger.warning(
                "no_available_packages",
                total_packages=len(packages),
                registry_types=[p.registry_type for p in packages],
            )
            return None

        if preference:
            for pkg in available:
                if pkg.registry_type == preference:
                    logger.debug(
                        "package_selected_by_preference",
                        registry_type=pkg.registry_type,
                        identifier=pkg.identifier,
                    )
                    return pkg

            logger.debug(
                "preference_not_available",
                preference=preference,
                available_types=[p.registry_type for p in available],
            )

        for registry_type in self.PRIORITY_ORDER:
            for pkg in available:
                if pkg.registry_type == registry_type:
                    logger.debug(
                        "package_selected_by_priority",
                        registry_type=pkg.registry_type,
                        identifier=pkg.identifier,
                    )
                    return pkg

        return available[0] if available else None

    def _filter_available(self, packages: list[PackageInfo]) -> list[PackageInfo]:
        """Filter packages to only those with available runtimes.

        Args:
            packages: List of packages to filter.

        Returns:
            List of packages with available runtimes.
        """
        available = []
        for pkg in packages:
            if self._is_runtime_available(pkg.registry_type):
                available.append(pkg)
        return available

    def _is_runtime_available(self, registry_type: str) -> bool:
        """Check if runtime for a registry type is available.

        Args:
            registry_type: The registry type to check.

        Returns:
            True if the runtime is available.
        """
        availability_map = {
            "pypi": self._availability.pypi,
            "npm": self._availability.npm,
            "oci": self._availability.oci,
            "mcpb": self._availability.binary,
        }
        return availability_map.get(registry_type, False)

    def get_available_runtimes(self) -> list[str]:
        """Get list of available runtime types.

        Returns:
            List of available registry types.
        """
        available = []
        if self._availability.pypi:
            available.append("pypi")
        if self._availability.npm:
            available.append("npm")
        if self._availability.oci:
            available.append("oci")
        if self._availability.binary:
            available.append("mcpb")
        return available


async def detect_runtime_availability(
    installers: list,
) -> RuntimeAvailability:
    """Detect which runtimes are available on the system.

    Args:
        installers: List of package installers to check.

    Returns:
        RuntimeAvailability with detected availability.
    """
    availability = RuntimeAvailability()

    for installer in installers:
        if installer.supports("pypi"):
            availability.pypi = await installer.is_runtime_available()
        elif installer.supports("npm"):
            availability.npm = await installer.is_runtime_available()
        elif installer.supports("oci"):
            availability.oci = await installer.is_runtime_available()
        elif installer.supports("mcpb"):
            availability.binary = await installer.is_runtime_available()

    logger.info(
        "runtime_availability_detected",
        pypi=availability.pypi,
        npm=availability.npm,
        oci=availability.oci,
        binary=availability.binary,
    )

    return availability
