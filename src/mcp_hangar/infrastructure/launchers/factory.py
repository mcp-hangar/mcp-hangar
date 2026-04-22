"""Factory function for mcp_server launchers."""

from .base import McpServerLauncher
from .container import ContainerLauncher
from .http import HttpLauncher
from .subprocess import SubprocessLauncher


def get_launcher(mode: str) -> McpServerLauncher:
    """
    Factory function to get the appropriate launcher for a mode.

    Args:
        mode: McpServer mode (subprocess, docker, container, podman, remote)

    Returns:
        Appropriate launcher instance

    Raises:
        ValueError: If mode is not supported
    """
    launchers = {
        "subprocess": SubprocessLauncher,
        "docker": lambda: ContainerLauncher(runtime="auto"),  # Use ContainerLauncher with auto-detection
        "container": lambda: ContainerLauncher(runtime="auto"),
        "podman": lambda: ContainerLauncher(runtime="podman"),
        "remote": HttpLauncher,
    }

    launcher_factory = launchers.get(mode)
    if not launcher_factory:
        raise ValueError(f"unsupported_mode: {mode}")

    launcher = launcher_factory() if callable(launcher_factory) else launcher_factory
    return launcher
