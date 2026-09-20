"""Docker/Podman Discovery Source.

Discovers MCP mcp_servers from Docker/Podman containers using labels.
Uses the same Docker API - works with both Docker and Podman.

Socket Detection Order:
    1. Explicit socket_path parameter
    2. DOCKER_HOST environment variable
    3. macOS: ~/.local/share/containers/podman/machine/podman.sock
    4. macOS: /var/folders/.../podman/podman-machine-default-api.sock
    5. Linux: /run/user/{uid}/podman/podman.sock (rootless Podman)
    6. Linux/macOS: /var/run/docker.sock (Docker)

Label Reference:
    mcp.hangar.enabled: "true"           # Required - enables discovery
    mcp.hangar.name: "my-mcp_server"       # Optional - defaults to container name
    mcp.hangar.mode: "container"         # Optional - container|http (default: container)
    mcp.hangar.port: "8080"              # For http mode only
    mcp.hangar.group: "tools"            # Optional - group membership
    mcp.hangar.command: "python app.py"  # Optional - override container command
    mcp.hangar.volumes: "/data:/data"    # Optional - additional volumes
"""

import asyncio
import os
import platform
import random
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource

from ...logging_config import get_logger

logger = get_logger(__name__)

# Optional Docker dependency (works with Podman too via Docker API compatibility)
try:
    import docker
    from docker.constants import DEFAULT_TIMEOUT_SECONDS
    from docker.errors import DockerException

    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
    DockerException = Exception
    DEFAULT_TIMEOUT_SECONDS = 60
    docker = None  # optional dependency: module unavailable


# Well-known socket locations
SOCKET_PATHS = {
    "docker": "/var/run/docker.sock",
    "podman_linux": "/run/user/{uid}/podman/podman.sock",
    "podman_macos_symlink": "~/.local/share/containers/podman/machine/podman.sock",
    "podman_macos_glob": "/var/folders/*/*/T/podman/podman-machine-default-api.sock",
}


def find_container_socket() -> str | None:
    """Find Docker or Podman socket.

    Returns:
        Socket path or None if not found
    """
    # 1. Check DOCKER_HOST env var
    docker_host = os.environ.get("DOCKER_HOST")
    if docker_host and docker_host.startswith("unix://"):
        socket_path = docker_host[7:]  # Remove "unix://"
        if Path(socket_path).exists():
            return socket_path

    # 2. Platform-specific detection
    if platform.system() == "Darwin":
        # macOS: Check Podman Machine symlink first
        podman_symlink = Path.home() / ".local/share/containers/podman/machine/podman.sock"
        if podman_symlink.exists():
            try:
                resolved = podman_symlink.resolve()
                if resolved.exists():
                    return str(resolved)
            except (OSError, RuntimeError):
                pass

        # macOS: Search in /var/folders for Podman socket
        import glob

        for pattern in [
            "/var/folders/*/*/T/podman/podman-machine-default-api.sock",
            "/var/folders/*/*/T/podman/podman-machine-default.sock",
        ]:
            for match in glob.glob(pattern):
                if Path(match).exists():
                    return match

    # 3. Linux: Check Podman rootless socket
    uid = os.getuid()
    podman_socket = f"/run/user/{uid}/podman/podman.sock"
    if Path(podman_socket).exists():
        return podman_socket

    # 4. Fallback: Docker socket
    if Path(SOCKET_PATHS["docker"]).exists():
        return SOCKET_PATHS["docker"]

    return None


_ABANDONED = "Docker discovery is stopping: connection attempts abandoned"


def _close_quietly(client: Any) -> None:
    """Close a docker client, ignoring whatever closing it raises."""
    try:
        client.close()
    except Exception:  # noqa: BLE001 -- infra-boundary: best-effort cleanup of a client nobody keeps
        pass


def _close_unclaimed(connecting: "Future[Any]") -> None:
    """Close the client a connection abandoned by a stop produced after all, if it did."""
    if not connecting.cancelled() and connecting.exception() is None:
        _close_quietly(connecting.result())


class DockerDiscoverySource(DiscoverySource):
    """Discover MCP mcp_servers from Docker/Podman containers.

    Works with both Docker and Podman through Docker API compatibility.
    Podman provides Docker-compatible API on its socket.

    Connecting never holds up discovery's event loop (#1464). The connection
    retry schedule runs on a daemon thread of its own: `start()` begins it and
    returns, and `discover()` awaits it without blocking the loop, so a stop
    runs at once, even during an attempt that the runtime never answers. The
    client, `self._client`, is only touched on the loop that runs discovery.
    """

    LABEL_PREFIX = "mcp.hangar."

    def __init__(
        self,
        mode: DiscoveryMode = DiscoveryMode.ADDITIVE,
        socket_path: str | None = None,
        default_ttl: int = 90,
        max_retries: int = 5,
        initial_backoff_s: float = 1.0,
        max_backoff_s: float = 30.0,
        connect_timeout_s: float = 5.0,
    ):
        """Initialize discovery source.

        Args:
            mode: Discovery mode (additive or authoritative)
            socket_path: Path to socket (None = auto-detect)
            default_ttl: Default TTL for discovered mcp_servers
            max_retries: Maximum connection retry attempts
            initial_backoff_s: Initial backoff delay in seconds
            max_backoff_s: Maximum backoff delay cap in seconds
            connect_timeout_s: Timeout of each request a connection attempt
                makes, instead of the client's default of 60 s. A connected
                client gets the default back.
        """
        super().__init__(mode)

        if not DOCKER_AVAILABLE:
            raise ImportError("docker package required. Install with: pip install docker")

        self._socket_path = socket_path
        self._default_ttl = default_ttl
        self._client: Any = None  # docker.DockerClient when available; touched only on discovery's loop
        self._max_retries = max_retries
        self._initial_backoff_s = initial_backoff_s
        self._max_backoff_s = max_backoff_s
        self._connect_timeout_s = connect_timeout_s
        self._known_container_ids: set[str] = set()
        #: Set by request_stop() and stop(), cleared by start(). The retry
        #: backoff waits on it, on the connection's own thread (#1436).
        self._stop_requested = threading.Event()
        #: The connection retry schedule in flight, if one is.
        self._connecting: Future[Any] | None = None
        #: Whether the runtime answered the last connection or scan. What
        #: health_check() reports; it reads, never connects.
        self._reachable = False

    def _connect_once(self, attempt: int) -> Any:
        """Make one connection attempt, and return a client whose ping answered.

        Each request the attempt makes has `connect_timeout_s` instead of the
        client's default of 60 s: the API version the client asks for when it
        is built, then the ping. The connected client gets the default back,
        so discovery's own calls time out exactly as before.
        """
        assert docker is not None  # guaranteed by __init__ DOCKER_AVAILABLE check

        socket = self._socket_path or find_container_socket()
        if socket:
            logger.info("docker_connecting", socket=socket, attempt=attempt + 1)
            client = docker.DockerClient(base_url=f"unix://{socket}", timeout=self._connect_timeout_s)
        else:
            logger.info("docker_connecting_from_env", attempt=attempt + 1)
            client = docker.from_env(timeout=self._connect_timeout_s)

        try:
            client.ping()
        except Exception:
            _close_quietly(client)
            raise
        client.api.timeout = DEFAULT_TIMEOUT_SECONDS
        return client

    def _connect_with_retries(self) -> Any:
        """Run the connection retry schedule, and return the connected client.

        Runs on the thread `_start_connecting` starts, never on discovery's
        loop. A stop ends the backoff wait between attempts, and no further
        attempt is made (#1436).
        """
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            if self._stop_requested.is_set():
                break
            try:
                client = self._connect_once(attempt)
                logger.info("docker_connected", attempt=attempt + 1)
                return client

            except (DockerException, OSError, ConnectionError) as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    delay = min(
                        self._max_backoff_s,
                        self._initial_backoff_s * (2**attempt),
                    )
                    jitter = delay * random.uniform(-0.1, 0.1)
                    sleep_time = max(0.1, delay + jitter)
                    logger.warning(
                        "docker_connection_retry",
                        attempt=attempt + 1,
                        max_retries=self._max_retries,
                        backoff_s=sleep_time,
                        error=str(e),
                    )
                    if self._stop_requested.wait(sleep_time):
                        break

        if self._stop_requested.is_set():
            logger.info("docker_connection_abandoned_on_stop", last_error=str(last_error))
            raise DockerException(_ABANDONED)

        logger.error(
            "docker_connection_exhausted",
            max_retries=self._max_retries,
            last_error=str(last_error),
        )
        raise DockerException(f"Failed to connect to Docker after {self._max_retries} attempts: {last_error}")

    def _start_connecting(self) -> "Future[Any]":
        """Start the retry schedule on a thread of its own, and return its future.

        The thread is a daemon, so one that a stop leaves in an attempt never
        keeps the process alive. Each request of that attempt is bounded by
        `connect_timeout_s`, and after it the stop ends the schedule.
        """
        connecting: Future[Any] = Future()
        # Running from the start, so a cancelled awaiter cannot cancel the
        # connection for another one awaiting it.
        connecting.set_running_or_notify_cancel()

        def run() -> None:
            try:
                connecting.set_result(self._connect_with_retries())
            except Exception as e:  # noqa: BLE001 -- infra-boundary: handed to whoever awaits the connection
                connecting.set_exception(e)

        threading.Thread(target=run, name="mcp-hangar-docker-connect", daemon=True).start()
        self._connecting = connecting
        return connecting

    async def _ensure_client(self) -> None:
        """Connect, unless connected, without holding up discovery's loop.

        The schedule runs on its own thread and this awaits it, so the loop is
        free while Docker is unreachable. A stop runs at once: it cancels the
        task awaiting here, and the attempt in flight finishes on its thread.
        """
        if self._client is not None:
            return
        if self._stop_requested.is_set():
            raise DockerException(_ABANDONED)

        connecting = self._connecting or self._start_connecting()
        try:
            client = await asyncio.wrap_future(connecting)
        except (DockerException, OSError, ConnectionError):
            self._reachable = False
            raise
        finally:
            if self._connecting is connecting and connecting.done():
                self._connecting = None

        if self._stop_requested.is_set():
            # Stopped while this waited: stop() took the connection over and
            # closes the client it produced.
            raise DockerException(_ABANDONED)
        self._client = client
        self._reachable = True

    @property
    def source_type(self) -> str:
        return "docker"

    async def discover(self) -> list[DiscoveredMcpServer]:
        """Discover mcp_servers from container labels with automatic reconnection."""
        try:
            await self._ensure_client()
        except (DockerException, OSError, ConnectionError) as e:
            logger.error("docker_discovery_connection_failed", error=str(e))
            return []  # Graceful degradation

        mcp_servers = []

        try:
            # Get all containers with MCP label (including stopped)
            containers = self._client.containers.list(all=True, filters={"label": f"{self.LABEL_PREFIX}enabled=true"})

            # Track container IDs to prevent duplicates after reconnection
            current_ids: set[str] = set()

            for container in containers:
                container_id = container.id[:12]
                current_ids.add(container_id)

                mcp_server = self._parse_container(container)
                if mcp_server:
                    mcp_servers.append(mcp_server)
                    await self.on_mcp_server_discovered(mcp_server)

            self._known_container_ids = current_ids
            self._reachable = True
            logger.debug(
                "docker_discovery_complete",
                mcp_servers_found=len(mcp_servers),
                containers_tracked=len(current_ids),
            )

        except (DockerException, OSError, ConnectionError) as e:
            logger.warning("docker_discovery_lost_connection", error=str(e))
            self._client = None  # Force reconnection on next call
            self._reachable = False
            return []  # Graceful degradation -- next discover() will reconnect

        return mcp_servers

    def _parse_container(self, container) -> DiscoveredMcpServer | None:
        """Parse container into DiscoveredMcpServer."""
        labels = container.labels or {}

        # Basic info
        name = labels.get(f"{self.LABEL_PREFIX}name", container.name)
        mode = labels.get(f"{self.LABEL_PREFIX}mode", "container")

        # Parse read-only setting (default: false for discovered containers)
        read_only_str = labels.get(f"{self.LABEL_PREFIX}read-only", "false").lower()
        read_only = read_only_str in ("true", "1", "yes")

        # Image info
        image_tags = getattr(container.image, "tags", []) or []
        image = image_tags[0] if image_tags else container.image.id[:12]

        # Build connection info based on mode
        if mode in ("container", "stdio", "subprocess"):
            # Container mode: MCP Hangar will run this image
            connection_info = {
                "image": image,
                "container_name": container.name,
                "read_only": read_only,
            }

            # Optional overrides
            if cmd := labels.get(f"{self.LABEL_PREFIX}command"):
                connection_info["command"] = cmd.split()
            if vols := labels.get(f"{self.LABEL_PREFIX}volumes"):
                connection_info["volumes"] = [v.strip() for v in vols.split(",")]

            mode = "container"  # Normalize

        elif mode in ("http", "sse"):
            # HTTP mode: connect to running container.
            #
            # The documented deployment (examples/quickstart/config.yaml) runs
            # Hangar on the host, so prefer the container's published host-port
            # binding (reachable from the host) over its internal bridge-network
            # IP (unreachable from the host in the common Podman-on-macOS case).
            port = int(labels.get(f"{self.LABEL_PREFIX}port", "8080"))
            resolved = self._get_host_endpoint(container, port)
            if not resolved:
                logger.debug(f"Container {name} has no IP, skipping")
                return None

            host, resolved_port = resolved
            connection_info = {
                "host": host,
                "port": resolved_port,
                "endpoint": f"http://{host}:{resolved_port}",
            }
        else:
            logger.warning(f"Unknown mode '{mode}' for container {name}")
            return None

        # Metadata
        metadata = {
            "container_id": container.id[:12],
            "container_name": container.name,
            "image": image,
            "status": container.status,
            "group": labels.get(f"{self.LABEL_PREFIX}group"),
            # Addresses the runtime itself reports for this container: the host
            # side of its published binding, and its network IP. Registration
            # refuses an endpoint resolving anywhere else, so a label cannot
            # point Hangar at another container or a host service and ride
            # discovery's trust to it (#771). Empty for a container-mode entry,
            # which has no endpoint to check.
            "runtime_addresses": self._runtime_addresses(container, connection_info.get("host")),
        }

        return DiscoveredMcpServer.create(
            name=name,
            source_type=self.source_type,
            mode=mode,
            connection_info=connection_info,
            metadata=metadata,
            ttl_seconds=int(labels.get(f"{self.LABEL_PREFIX}ttl", self._default_ttl)),
        )

    def _runtime_addresses(self, container: Any, endpoint_host: Any) -> list[str]:
        """Addresses this container is reachable at according to the runtime.

        Both entries are read from `container.attrs`, never from a label: the
        host side of a published port binding, and the container's own network
        IP. Which one the endpoint uses depends on where Hangar runs, and both
        are legitimate -- what matters is that neither came from the container's
        own claims about itself.
        """
        addresses: list[str] = []
        if isinstance(endpoint_host, str) and endpoint_host:
            addresses.append(endpoint_host)
        if (ip := self._get_container_ip(container)) and ip not in addresses:
            addresses.append(ip)
        return addresses

    def _get_container_ip(self, container: Any) -> str | None:
        """Get container IP address from any network."""
        try:
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_info in networks.values():
                if ip := net_info.get("IPAddress"):
                    return str(ip)
        except Exception:  # noqa: BLE001 -- infra-boundary: best-effort container IP extraction
            pass
        return None

    def _get_host_endpoint(self, container: Any, port: int) -> tuple[str, int] | None:
        """Resolve a reachable (host, port) for a container's labeled port.

        Prefers the published host-port binding from ``NetworkSettings.Ports``
        (e.g. ``-p 18080:8080``), which is reachable from a host-mode Hangar
        process -- the only documented deployment topology for container
        discovery. Falls back to the container's internal bridge-network IP
        and the container-side port only when there is no host binding (e.g.
        Hangar itself runs as a container on the same network as the
        discovered container).

        Args:
            container: Docker/Podman container object.
            port: Container-side port from the ``mcp.hangar.port`` label.

        Returns:
            (host, port) tuple reachable from the host, or None if the
            container has neither a published host port nor a network IP
            (e.g. still starting or stopped).
        """
        try:
            ports = container.attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
        except Exception:  # noqa: BLE001 -- infra-boundary: best-effort port-mapping extraction
            ports = {}

        for proto in ("tcp", "udp"):
            for binding in ports.get(f"{port}/{proto}") or []:
                host_port = binding.get("HostPort")
                if not host_port:
                    continue
                try:
                    resolved_port = int(host_port)
                except (TypeError, ValueError):
                    continue
                host_ip = binding.get("HostIp") or "127.0.0.1"
                if host_ip in ("0.0.0.0", "::", ""):
                    host_ip = "127.0.0.1"
                return host_ip, resolved_port

        # No published host binding -- fall back to the internal network IP
        # (reachable only when Hangar shares the container's network).
        ip = self._get_container_ip(container)
        if ip:
            return ip, port

        return None

    async def health_check(self) -> bool:
        """Report whether the runtime answered when discovery last reached it.

        No I/O. The source listing (`GET /discovery/sources`, `hangar_sources`)
        calls this on the event loop serving that request, not on discovery's.
        It used to connect and ping from there: while Docker was unreachable
        that held up the serving loop for the whole retry schedule, and it set
        the client discovery's thread was using (#1464).

        Returns:
            True if the last connection or scan reached Docker/Podman. False
            before the first one, after one failed, and once stopped.
        """
        return self._reachable

    def request_stop(self) -> None:
        """End the connection schedule: its backoff wait ends, and no attempt follows.

        Safe from any thread. Until `start()` runs again, connecting is refused.
        """
        self._stop_requested.set()

    async def start(self) -> None:
        """Start discovery source: begin connecting, without waiting for it.

        The first `discover()` awaits that same connection, so a reachable
        runtime is still scanned in the first discovery cycle.
        """
        self._stop_requested.clear()
        if self._client is None and self._connecting is None:
            self._start_connecting()

    async def stop(self) -> None:
        """Stop discovery source, without waiting for a connection attempt in flight."""
        self._stop_requested.set()
        self._reachable = False
        connecting, self._connecting = self._connecting, None
        if connecting is not None:
            # Left to finish on its own thread; a client it produces after all
            # is closed rather than kept.
            connecting.add_done_callback(_close_unclaimed)
        if self._client is not None:
            _close_quietly(self._client)
            self._client = None
