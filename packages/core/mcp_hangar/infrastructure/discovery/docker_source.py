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
import platform
from pathlib import Path

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
    ):
        """Initialize discovery source.

        Args:
            mode: Discovery mode (additive or authoritative)
            socket_path: Path to socket (None = auto-detect)
            default_ttl: Default TTL for discovered providers
        """
        super().__init__(mode)

        if not DOCKER_AVAILABLE:
            raise ImportError("docker package required. Install with: pip install docker")

        self._socket_path = socket_path
        self._default_ttl = default_ttl
        self._client: docker.DockerClient | None = None

    def _ensure_client(self) -> None:
        """Ensure Docker client is connected."""
        if self._client is not None:
            return

        # Find socket
        socket = self._socket_path or find_container_socket()

        if socket:
            logger.info(f"Connecting to container runtime at: {socket}")
            self._client = docker.DockerClient(base_url=f"unix://{socket}")
        else:
            # Last resort: let docker library figure it out
            logger.info("Using docker.from_env() for container runtime")
            self._client = docker.from_env()

    @property
    def source_type(self) -> str:
        return "docker"

    async def discover(self) -> list[DiscoveredProvider]:
        """Discover providers from container labels."""
        self._ensure_client()
        providers = []

        try:
            # Get all containers with MCP label (including stopped)
            containers = self._client.containers.list(all=True, filters={"label": f"{self.LABEL_PREFIX}enabled=true"})

            for container in containers:
                provider = self._parse_container(container)
                if provider:
                    providers.append(provider)
                    await self.on_provider_discovered(provider)

            logger.debug(f"Discovered {len(providers)} providers from containers")

        except DockerException as e:
            logger.error(f"Container discovery failed: {e}")
            raise

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

    def _get_container_ip(self, container) -> str | None:
        """Get container IP address from any network."""
        try:
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_info in networks.values():
                if ip := net_info.get("IPAddress"):
                    return ip
        except Exception:
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
        except Exception as e:
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
            except Exception:
                pass
            self._client = None
