"""Docker/Podman Discovery Source.

Discovers MCP providers from Docker/Podman containers using labels.
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
    mcp.hangar.name: "my-provider"       # Optional - defaults to container name
    mcp.hangar.mode: "container"         # Optional - container|http (default: container)
    mcp.hangar.port: "8080"              # For http mode only
    mcp.hangar.group: "tools"            # Optional - group membership
    mcp.hangar.command: "python app.py"  # Optional - override container command
    mcp.hangar.volumes: "/data:/data"    # Optional - additional volumes
"""

import os
from pathlib import Path
import platform
import random
import time
from typing import Any

from mcp_hangar.domain.discovery.discovered_provider import DiscoveredProvider
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource

from ...logging_config import get_logger

logger = get_logger(__name__)

# Optional Docker dependency (works with Podman too via Docker API compatibility)
try:
    import docker
    from docker.errors import DockerException

    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
    DockerException = Exception
    docker = None


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


class DockerDiscoverySource(DiscoverySource):
    """Discover MCP providers from Docker/Podman containers.

    Works with both Docker and Podman through Docker API compatibility.
    Podman provides Docker-compatible API on its socket.
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
    ):
        """Initialize discovery source.

        Args:
            mode: Discovery mode (additive or authoritative)
            socket_path: Path to socket (None = auto-detect)
            default_ttl: Default TTL for discovered providers
            max_retries: Maximum connection retry attempts
            initial_backoff_s: Initial backoff delay in seconds
            max_backoff_s: Maximum backoff delay cap in seconds
        """
        super().__init__(mode)

        if not DOCKER_AVAILABLE:
            raise ImportError("docker package required. Install with: pip install docker")

        self._socket_path = socket_path
        self._default_ttl = default_ttl
        self._client: Any = None  # docker.DockerClient when available
        self._max_retries = max_retries
        self._initial_backoff_s = initial_backoff_s
        self._max_backoff_s = max_backoff_s
        self._known_container_ids: set[str] = set()

    def _ensure_client(self) -> None:
        """Ensure Docker client is connected, retrying with backoff on failure."""
        if self._client is not None:
            return

        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                socket = self._socket_path or find_container_socket()

                if socket:
                    logger.info("docker_connecting", socket=socket, attempt=attempt + 1)
                    self._client = docker.DockerClient(base_url=f"unix://{socket}")
                else:
                    logger.info("docker_connecting_from_env", attempt=attempt + 1)
                    self._client = docker.from_env()

                # Verify connection works
                self._client.ping()
                logger.info("docker_connected", attempt=attempt + 1)
                return

            except (DockerException, OSError, ConnectionError) as e:
                last_error = e
                self._client = None  # Reset on failure
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
                    time.sleep(sleep_time)

        logger.error(
            "docker_connection_exhausted",
            max_retries=self._max_retries,
            last_error=str(last_error),
        )
        raise DockerException(f"Failed to connect to Docker after {self._max_retries} attempts: {last_error}")

    def _reconnect(self) -> None:
        """Force reconnection by closing existing client and retrying."""
        if self._client:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 -- infra-boundary: best-effort cleanup before reconnect
                pass
            self._client = None

        self._ensure_client()

    @property
    def source_type(self) -> str:
        return "docker"

    async def discover(self) -> list[DiscoveredProvider]:
        """Discover providers from container labels with automatic reconnection."""
        try:
            self._ensure_client()
        except (DockerException, OSError, ConnectionError) as e:
            logger.error("docker_discovery_connection_failed", error=str(e))
            return []  # Graceful degradation

        providers = []

        try:
            # Get all containers with MCP label (including stopped)
            containers = self._client.containers.list(all=True, filters={"label": f"{self.LABEL_PREFIX}enabled=true"})

            # Track container IDs to prevent duplicates after reconnection
            current_ids: set[str] = set()

            for container in containers:
                container_id = container.id[:12]
                current_ids.add(container_id)

                provider = self._parse_container(container)
                if provider:
                    providers.append(provider)
                    await self.on_provider_discovered(provider)

            self._known_container_ids = current_ids
            logger.debug(
                "docker_discovery_complete",
                providers_found=len(providers),
                containers_tracked=len(current_ids),
            )

        except (DockerException, OSError, ConnectionError) as e:
            logger.warning("docker_discovery_lost_connection", error=str(e))
            self._client = None  # Force reconnection on next call
            return []  # Graceful degradation -- next discover() will reconnect

        return providers

    def _parse_container(self, container) -> DiscoveredProvider | None:
        """Parse container into DiscoveredProvider."""
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
            # HTTP mode: connect to running container
            ip = self._get_container_ip(container)
            if not ip:
                logger.warning(f"Container {name} has no IP, skipping")
                return None

            port = int(labels.get(f"{self.LABEL_PREFIX}port", "8080"))
            connection_info = {
                "host": ip,
                "port": port,
                "endpoint": f"http://{ip}:{port}",
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
        }

        return DiscoveredProvider.create(
            name=name,
            source_type=self.source_type,
            mode=mode,
            connection_info=connection_info,
            metadata=metadata,
            ttl_seconds=int(labels.get(f"{self.LABEL_PREFIX}ttl", self._default_ttl)),
        )

    def _get_container_ip(self, container: Any) -> str | None:
        """Get container IP address from any network."""
        try:
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_info in networks.values():
                if ip := net_info.get("IPAddress"):
                    return ip
        except Exception:  # noqa: BLE001 -- infra-boundary: best-effort container IP extraction
            pass
        return None

    async def health_check(self) -> bool:
        """Check if container runtime is accessible.

        Returns:
            True if Docker/Podman is accessible, False otherwise.
        """
        try:
            self._ensure_client()
            self._client.ping()
            return True
        except (OSError, ConnectionError, RuntimeError, TimeoutError) as e:
            logger.warning(f"Container runtime health check failed: {e}")
            return False
        except Exception as e:  # noqa: BLE001 -- infra-boundary: health check returns unhealthy on error
            # Docker client can raise various exceptions depending on version
            # Log and return False for any connection-related failure
            logger.warning(f"Container runtime health check failed: {type(e).__name__}: {e}")
            return False

    async def start(self) -> None:
        """Start discovery source."""
        self._ensure_client()

    async def stop(self) -> None:
        """Stop discovery source."""
        if self._client:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 -- infra-boundary: best-effort cleanup on close
                pass
