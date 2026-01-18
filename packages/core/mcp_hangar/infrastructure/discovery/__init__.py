"""Discovery infrastructure module.

This module contains infrastructure implementations for provider discovery
from various sources: Kubernetes, Docker, Filesystem, and Python entrypoints.

Note: Each source has optional dependencies. Import errors are handled gracefully.
"""

from typing import TYPE_CHECKING

# Lazy imports to handle optional dependencies
_kubernetes_source = None
_docker_source = None
_filesystem_source = None
_entrypoint_source = None


def _get_kubernetes_source():
    """Get KubernetesDiscoverySource class, importing lazily."""
    global _kubernetes_source
    if _kubernetes_source is None:
        try:
            from .kubernetes_source import KubernetesDiscoverySource

            _kubernetes_source = KubernetesDiscoverySource
        except ImportError:
            _kubernetes_source = None
    return _kubernetes_source


def _get_docker_source():
    """Get DockerDiscoverySource class, importing lazily."""
    global _docker_source
    if _docker_source is None:
        try:
            from .docker_source import DockerDiscoverySource

            _docker_source = DockerDiscoverySource
        except ImportError:
            _docker_source = None
    return _docker_source


def _get_filesystem_source():
    """Get FilesystemDiscoverySource class, importing lazily."""
    global _filesystem_source
    if _filesystem_source is None:
        try:
            from .filesystem_source import FilesystemDiscoverySource

            _filesystem_source = FilesystemDiscoverySource
        except ImportError:
            _filesystem_source = None
    return _filesystem_source


def _get_entrypoint_source():
    """Get EntrypointDiscoverySource class, importing lazily."""
    global _entrypoint_source
    if _entrypoint_source is None:
        try:
            from .entrypoint_source import EntrypointDiscoverySource

            _entrypoint_source = EntrypointDiscoverySource
        except ImportError:
            _entrypoint_source = None
    return _entrypoint_source


# For type checking and IDE support
if TYPE_CHECKING:
    from .docker_source import DockerDiscoverySource
    from .entrypoint_source import EntrypointDiscoverySource
    from .filesystem_source import FilesystemDiscoverySource
    from .kubernetes_source import KubernetesDiscoverySource


def __getattr__(name: str):
    """Lazy attribute access for discovery sources."""
    if name == "KubernetesDiscoverySource":
        cls = _get_kubernetes_source()
        if cls is None:
            raise ImportError(
                "KubernetesDiscoverySource requires 'kubernetes' package. Install with: pip install kubernetes"
            )
        return cls
    elif name == "DockerDiscoverySource":
        cls = _get_docker_source()
        if cls is None:
            raise ImportError("DockerDiscoverySource requires 'docker' package. Install with: pip install docker")
        return cls
    elif name == "FilesystemDiscoverySource":
        cls = _get_filesystem_source()
        if cls is None:
            raise ImportError("FilesystemDiscoverySource requires 'pyyaml' package. Install with: pip install pyyaml")
        return cls
    elif name == "EntrypointDiscoverySource":
        cls = _get_entrypoint_source()
        if cls is None:
            raise ImportError("EntrypointDiscoverySource requires 'importlib-metadata' package")
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "KubernetesDiscoverySource",
    "DockerDiscoverySource",
    "FilesystemDiscoverySource",
    "EntrypointDiscoverySource",
]
