"""Docker container network connection monitor -- BSL 1.1 licensed.

Discovers TCP connections from Docker containers by executing ``ss -tnp``
(preferred) or reading ``/proc/net/tcp`` (fallback) inside the container.
Containers are identified by the ``mcp-hangar.provider-id`` label injected
during launch.

See enterprise/LICENSE.BSL for license terms.
"""

from __future__ import annotations

import time

import structlog

from mcp_hangar.domain.value_objects.behavioral import NetworkObservation

from .proc_net_parser import parse_proc_net_tcp, parse_ss_output

logger = structlog.get_logger(__name__)

try:
    import docker

    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False


class DockerNetworkMonitor:
    """Poll TCP connections from Docker containers labeled with a provider ID.

    Uses the Docker SDK to exec into running containers and extract
    connection information via ``ss -tnp`` (preferred) with automatic
    fallback to ``/proc/net/tcp`` when ``ss`` is unavailable.

    The availability of ``ss`` is cached per container (by short ID) so
    repeated polls skip the probe once the result is known.
    """

    def __init__(self) -> None:
        """Initialize with a Docker client from the environment.

        Raises:
            ImportError: If the ``docker`` package is not installed.
        """
        if not DOCKER_AVAILABLE:
            raise ImportError("docker package is required for DockerNetworkMonitor. Install with: pip install docker")
        self._client = docker.from_env()
        self._ss_available: dict[str, bool] = {}

    def poll_connections(self, provider_id: str) -> list[NetworkObservation]:
        """Poll active TCP connections for a provider's container.

        Finds the running container labeled ``mcp-hangar.provider-id={provider_id}``,
        executes a connection discovery command inside it, and converts the
        raw output into ``NetworkObservation`` records.

        Args:
            provider_id: The provider identifier to look up by container label.

        Returns:
            List of ``NetworkObservation`` records for established outbound
            connections. Returns an empty list when no matching container is
            found or when connection discovery fails.
        """
        containers = self._client.containers.list(
            filters={
                "label": f"mcp-hangar.provider-id={provider_id}",
                "status": "running",
            }
        )
        if not containers:
            logger.debug(
                "no_container_found",
                provider_id=provider_id,
            )
            return []

        container = containers[0]
        connections = self._get_connections(container)
        now = time.time()

        return [
            NetworkObservation(
                timestamp=now,
                provider_id=provider_id,
                destination_host=host,
                destination_port=port,
                protocol=protocol,
                direction="outbound",
            )
            for host, port, protocol in connections
        ]

    def _get_connections(self, container: object) -> list[tuple[str, int, str]]:
        """Extract TCP connections from a container via ss or /proc/net/tcp.

        Tries ``ss -tnp`` first. If the command fails (non-zero exit or
        exception), caches the failure and falls back to reading
        ``/proc/net/tcp``.

        Args:
            container: A Docker container object (from the Docker SDK).

        Returns:
            List of (host, port, protocol) tuples for established
            non-loopback connections.
        """
        cid = container.short_id  # type: ignore[attr-defined]

        # Try ss if not known to be unavailable
        if self._ss_available.get(cid, True):
            try:
                exit_code, output = container.exec_run("ss -tnp", demux=True)  # type: ignore[attr-defined]
                if exit_code == 0 and output[0]:
                    self._ss_available[cid] = True
                    return parse_ss_output(output[0].decode("utf-8"))
                else:
                    self._ss_available[cid] = False
            except Exception:  # noqa: BLE001
                logger.debug("ss_exec_failed", container_id=cid)
                self._ss_available[cid] = False

        # Fallback to /proc/net/tcp
        try:
            exit_code, output = container.exec_run("cat /proc/net/tcp", demux=True)  # type: ignore[attr-defined]
            if exit_code == 0 and output[0]:
                return parse_proc_net_tcp(output[0].decode("utf-8"))
        except Exception:  # noqa: BLE001
            logger.debug("proc_net_tcp_read_failed", container_id=cid)

        return []
