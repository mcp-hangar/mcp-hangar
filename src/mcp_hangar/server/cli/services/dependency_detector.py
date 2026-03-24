"""Dependency detection for CLI commands.

Detects available runtimes (npx, uvx, docker, podman) and filters
providers based on what can actually be executed.
"""

from dataclasses import dataclass
from functools import lru_cache
import shutil


@dataclass(frozen=True)
class RuntimeInfo:
    """Information about a detected runtime."""

    name: str
    path: str | None
    available: bool
    version: str | None = None


@dataclass
class DependencyStatus:
    """Status of all detected dependencies."""

    npx: RuntimeInfo
    uvx: RuntimeInfo
    docker: RuntimeInfo
    podman: RuntimeInfo

    @property
    def has_any(self) -> bool:
        """Check if any runtime is available."""
        return any([self.npx.available, self.uvx.available, self.docker.available, self.podman.available])

    @property
    def has_container_runtime(self) -> bool:
        """Check if docker or podman is available."""
        return self.docker.available or self.podman.available

    @property
    def available_runtimes(self) -> list[str]:
        """Get list of available runtime names."""
        result = []
        if self.npx.available:
            result.append("npx")
        if self.uvx.available:
            result.append("uvx")
        if self.docker.available:
            result.append("docker")
        if self.podman.available:
            result.append("podman")
        return result

    @property
    def missing_runtimes(self) -> list[str]:
        """Get list of missing runtime names."""
        result = []
        if not self.npx.available:
            result.append("npx")
        if not self.uvx.available:
            result.append("uvx")
        if not self.docker.available and not self.podman.available:
            result.append("docker/podman")
        return result


def _detect_runtime(name: str) -> RuntimeInfo:
    """Detect if a runtime is available in PATH.

    Args:
        name: Runtime executable name (npx, uvx, docker, podman)

    Returns:
        RuntimeInfo with detection results
    """
    path = shutil.which(name)
    return RuntimeInfo(
        name=name,
        path=path,
        available=path is not None,
    )


@lru_cache(maxsize=1)
def detect_dependencies() -> DependencyStatus:
    """Detect all available dependencies.

    Results are cached for the lifetime of the process.

    Returns:
        DependencyStatus with all runtime information
    """
    return DependencyStatus(
        npx=_detect_runtime("npx"),
        uvx=_detect_runtime("uvx"),
        docker=_detect_runtime("docker"),
        podman=_detect_runtime("podman"),
    )


def is_provider_available(install_type: str, deps: DependencyStatus | None = None) -> bool:
    """Check if a provider can be installed given available dependencies.

    Args:
        install_type: Provider's install_type (npx, uvx, docker, binary)
        deps: Optional pre-detected dependencies (uses cached if None)

    Returns:
        True if provider can be installed
    """
    if deps is None:
        deps = detect_dependencies()

    availability_map = {
        "npx": deps.npx.available,
        "uvx": deps.uvx.available,
        "docker": deps.has_container_runtime,
        "binary": True,  # Binary providers are self-contained
    }
    return availability_map.get(install_type, True)  # Unknown types default to available


def get_install_instructions(missing: list[str]) -> dict[str, str]:
    """Get installation instructions for missing dependencies.

    Args:
        missing: List of missing runtime names

    Returns:
        Dict mapping runtime name to installation instructions
    """
    instructions = {
        "npx": "Install Node.js: https://nodejs.org/ or `brew install node`",
        "uvx": "Install uv: https://docs.astral.sh/uv/ or `curl -LsSf https://astral.sh/uv/install.sh | sh`",
        "docker/podman": "Install Docker: https://docker.com/ or Podman: https://podman.io/",
        "docker": "Install Docker: https://docker.com/",
        "podman": "Install Podman: https://podman.io/",
    }
    return {name: instructions.get(name, f"Install {name}") for name in missing}


def clear_cache() -> None:
    """Clear the dependency detection cache.

    Useful for testing or when environment changes.
    """
    detect_dependencies.cache_clear()
