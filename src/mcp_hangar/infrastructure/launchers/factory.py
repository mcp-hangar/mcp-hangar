"""Factory function for mcp_server launchers, and who may use the local ones.

`subprocess` and `docker` are not servers the gateway talks to -- they are
servers it *runs*, as child processes with their stdio attached. That makes
them a property of one process rather than of the fleet, and it is why they are
owned by the instance holding the management lease when there is one.

The gate lives here because this is the one place every launch goes through.
"""

from collections.abc import Callable
from typing import cast

from mcp_hangar.logging_config import get_logger

from .base import McpServerLauncher
from .container import ContainerLauncher
from .http import HttpLauncher
from .subprocess import SubprocessLauncher

logger = get_logger(__name__)

#: Modes whose server is a child of this process: a pipe, not an address. A
#: peer cannot reach one, which is why they cannot be shared and why a follower
#: starting its own copy is a second server rather than a second route to the
#: first.
LOCAL_MODES = frozenset({"subprocess", "docker", "container", "podman"})

_may_launch_local: Callable[[], bool] | None = None


class LocalModeNotOwnedError(RuntimeError):
    """A follower tried to start a server that belongs to the lease holder.

    Raised rather than started. Starting it would be the quiet answer and the
    wrong one: the follower would get a *second* copy of the server, with its
    own child process and its own mounted volumes -- two writers to a store
    that expects one, and a fleet where the answer depends on which replica the
    request reached.
    """

    def __init__(self, mode: str) -> None:
        super().__init__(
            f"{mode} servers are run by the instance holding the management lease, and this instance does not "
            "hold it. A server in this mode is a child process of one gateway, so a peer cannot reach it and "
            "starting a local copy would be a second server rather than a second route to the first. Use "
            "`remote` mode for servers that several replicas must serve."
        )


def set_local_mode_policy(may_launch: Callable[[], bool] | None) -> None:
    """Say who may start local-mode servers in this process.

    Called by the composition root. `None` means nobody is coordinating -- a
    standalone gateway -- and every mode is available, which is what every
    deployment that has not selected a storage backend gets.

    Args:
        may_launch: Whether this instance may start local-mode servers now.
            Asked per launch, not once: a lease lost mid-life has to stop the
            next start, not the next process.
    """
    global _may_launch_local
    _may_launch_local = may_launch


def get_launcher(mode: str) -> McpServerLauncher:
    """
    Factory function to get the appropriate launcher for a mode.

    Args:
        mode: McpServer mode (subprocess, docker, container, podman, remote)

    Returns:
        Appropriate launcher instance

    Raises:
        ValueError: If mode is not supported
        LocalModeNotOwnedError: If the mode runs a child process and this
            instance is coordinating with peers but does not hold the lease.
    """
    if mode in LOCAL_MODES and _may_launch_local is not None and not _may_launch_local():
        logger.warning("local_mode_launch_refused", mode=mode)
        raise LocalModeNotOwnedError(mode)

    launchers = {
        "subprocess": SubprocessLauncher,
        "docker": lambda: ContainerLauncher(runtime="auto"),
        "container": lambda: ContainerLauncher(runtime="auto"),
        "podman": lambda: ContainerLauncher(runtime="podman"),
        "remote": HttpLauncher,
    }

    launcher_factory = launchers.get(mode)
    if not launcher_factory:
        raise ValueError(f"unsupported_mode: {mode}")

    launcher = launcher_factory() if callable(launcher_factory) else launcher_factory
    return cast(McpServerLauncher, launcher)
