"""Container (Docker/Podman) provider launcher implementation.

Note on CI volume mounts:
Some CI environments mount the workspace with restrictive permissions that can
cause containerized providers to fail writing to bind-mounted directories. We
support an opt-in behavior to chmod mounted host directories to be writable
before launching containers (see MCP_CI_RELAX_VOLUME_PERMS).

Debugging container startup:
By default we capture container stderr so the parent process can read it.
To make startup failures visible directly in CI logs, you can enable
MCP_CONTAINER_INHERIT_STDERR=true to inherit stderr (and use stderr=None in
subprocess.Popen); otherwise stderr remains captured.
"""

from dataclasses import dataclass, field
import os
import shutil
import subprocess

from mcp_hangar.logging_config import get_logger
from mcp_hangar.stdio_client import StdioClient
from mcp_hangar.domain.exceptions import ProviderStartError, ValidationError
from mcp_hangar.domain.security.input_validator import InputValidator
from mcp_hangar.domain.security.sanitizer import Sanitizer
from .base import ProviderLauncher

logger = get_logger(__name__)


@dataclass
class ContainerConfig:
    """Configuration for container-based provider launch."""

    image: str
    command: list[str] | None = None  # Override container entrypoint
    args: list[str] | None = None  # Arguments passed to container command
    volumes: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    memory_limit: str = "512m"
    cpu_limit: str = "1.0"
    network: str = "none"  # none, bridge, host
    read_only: bool = True
    drop_capabilities: bool = False  # Disabled: causes issues with native modules (e.g., better-sqlite3)
    user: str | None = None  # Run as specific user
    provider_id: str | None = None  # Provider identity for container discovery


class ContainerLauncher(ProviderLauncher):
    """
    Unified launcher for Docker/Podman containers.

    Supports both Docker and Podman with auto-detection.
    Podman is preferred when available (rootless by default = more secure).

    Features:
    - Auto-detect container runtime
    - Volume mounts with security validation
    - Resource limits (memory, CPU)
    - Network isolation
    - Security hardening (drop capabilities, no-new-privileges)
    """

    # Paths that are never allowed to be mounted
    BLOCKED_MOUNT_PATHS: set[str] = {
        "/",
        "/etc",
        "/var",
        "/usr",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        "/boot",
        "/root",
        "/sys",
        "/proc",
    }

    # Docker/Podman images that are always blocked
    BLOCKED_IMAGES: set[str] = {
        "ubuntu",
        "debian",
        "alpine",
        "busybox",
    }

    def __init__(
        self,
        runtime: str = "auto",
        allowed_registries: set[str] | None = None,
        blocked_images: set[str] | None = None,
        allowed_mount_paths: set[str] | None = None,
    ):
        """
        Initialize container launcher.

        Args:
            runtime: Container runtime ("auto", "podman", "docker")
            allowed_registries: Whitelist of allowed registries
            blocked_images: Images that cannot be run
            allowed_mount_paths: Whitelist of paths that can be mounted
        """
        # Allow CI / operators to force a specific runtime.
        # Useful for stabilizing environments where both podman and docker exist,
        # but podman rootless volume semantics can differ.
        forced_runtime = os.getenv("MCP_CONTAINER_RUNTIME")
        if forced_runtime:
            runtime = forced_runtime.strip().lower()

        self._runtime = self._detect_runtime(runtime)
        self._allowed_registries = allowed_registries
        self._blocked_images = blocked_images or self.BLOCKED_IMAGES
        self._allowed_mount_paths = allowed_mount_paths

        self._validator = InputValidator()
        self._sanitizer = Sanitizer()

        logger.info(f"ContainerLauncher initialized with runtime: {self._runtime}")

    @property
    def runtime(self) -> str:
        """Get the container runtime being used."""
        return self._runtime

    def _detect_runtime(self, preference: str) -> str:
        """
        Detect available container runtime.

        Prefers podman over docker (rootless by default).

        Args:
            preference: "auto", "podman", or "docker"

        Returns:
            Runtime command name (full path if needed)

        Raises:
            ProviderStartError: If no runtime found
        """
        runtime_path = self._find_runtime(preference)
        if runtime_path:
            return runtime_path

        if preference != "auto":
            raise ProviderStartError(
                provider_id="container_launcher",
                reason=f"Container runtime '{preference}' not found in PATH",
            )

        raise ProviderStartError(
            provider_id="container_launcher",
            reason="No container runtime found. Install podman or docker.",
        )

    def _find_runtime(self, preference: str) -> str | None:
        """
        Find container runtime executable.

        Checks standard paths in addition to PATH, which helps when
        running from environments with restricted PATH (e.g., Claude Desktop on macOS).
        """
        # Standard paths where container runtimes are installed
        extra_paths = [
            "/opt/podman/bin",  # macOS Podman installer
            "/usr/local/bin",
            "/opt/homebrew/bin",  # Homebrew on Apple Silicon
            "/usr/bin",
        ]

        runtimes_to_check = []
        if preference == "auto":
            runtimes_to_check = ["podman", "docker"]  # Prefer podman
        else:
            runtimes_to_check = [preference]

        for runtime in runtimes_to_check:
            # First check PATH
            path = shutil.which(runtime)
            if path:
                return path

            # Check extra paths
            for extra_path in extra_paths:
                full_path = os.path.join(extra_path, runtime)
                if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
                    return full_path

        return None

    def _validate_image(self, image: str) -> None:
        """Validate container image name."""
        result = self._validator.validate_docker_image(image)

        if not result.valid:
            errors = "; ".join(e.message for e in result.errors)
            raise ValidationError(message=f"Image validation failed: {errors}", field="image", value=image)

        # Check blocked images
        image_name = image.split(":")[0].split("/")[-1]
        if image_name in self._blocked_images:
            raise ValidationError(message=f"Image '{image_name}' is blocked", field="image", value=image)

        # Check registry whitelist
        if self._allowed_registries:
            parts = image.split("/")
            if len(parts) > 1 and "." in parts[0]:
                registry = parts[0]
            else:
                registry = "docker.io"

            if registry not in self._allowed_registries:
                raise ValidationError(
                    message=f"Registry '{registry}' is not allowed",
                    field="image",
                    value=image,
                )

    def _validate_volume(self, volume: str) -> None:
        """
        Validate a volume mount specification.

        Format: host_path:container_path[:ro|rw]

        Raises:
            ValidationError: If volume mount is not allowed
        """
        parts = volume.split(":")
        if len(parts) < 2:
            raise ValidationError(
                message="Invalid volume format. Use: host_path:container_path[:ro]",
                field="volume",
                value=volume,
            )

        host_path = parts[0]

        # Expand environment variables and user home
        host_path = os.path.expandvars(os.path.expanduser(host_path))

        # Check blocked paths
        for blocked in self.BLOCKED_MOUNT_PATHS:
            if host_path == blocked or host_path.startswith(blocked + "/"):
                # Allow if it's deep enough (e.g., /var/data is ok, /var is not)
                depth = len(host_path.split("/"))
                if depth <= 3 and blocked != "/":
                    raise ValidationError(
                        message=f"Mounting '{host_path}' is not allowed (system path)",
                        field="volume",
                        value=volume,
                    )

        # Check allowed paths whitelist
        if self._allowed_mount_paths:
            allowed = False
            for allowed_path in self._allowed_mount_paths:
                expanded = os.path.expandvars(os.path.expanduser(allowed_path))
                if host_path.startswith(expanded):
                    allowed = True
                    break

            if not allowed:
                raise ValidationError(
                    message=f"Path '{host_path}' is not in allowed mount paths",
                    field="volume",
                    value=volume,
                )

    def _build_command(self, config: ContainerConfig) -> list[str]:
        """
        Build container run command with security options.

        Args:
            config: Container configuration

        Returns:
            Complete command as list
        """
        cmd = [self._runtime, "run", "--rm", "-i"]

        # Network isolation
        if config.network == "none":
            cmd.extend(["--network", "none"])
        elif config.network == "bridge":
            cmd.extend(["--network", "bridge"])
        elif config.network == "host":
            cmd.extend(["--network", "host"])

        # Resource limits
        if config.memory_limit:
            cmd.extend(["--memory", config.memory_limit])

        if config.cpu_limit:
            cmd.extend(["--cpus", config.cpu_limit])

        # Security options
        #
        # Even with a read-write root filesystem, some providers rely on standard
        # writable temp locations like /tmp during startup. In hardened modes,
        # /tmp may not exist or may not be writable depending on the image.
        #
        # We always provide a writable tmpfs at /tmp for stability and to avoid
        # writing to the container layer.
        if config.read_only:
            cmd.append("--read-only")

        # Add tmpfs for directories that need to be writable
        cmd.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"])

        if config.drop_capabilities:
            cmd.extend(["--cap-drop", "ALL"])

        # No new privileges
        cmd.extend(["--security-opt", "no-new-privileges"])

        # User
        if config.user:
            cmd.extend(["--user", config.user])

        # Volume mounts
        for volume in config.volumes:
            # Expand variables in host path
            parts = volume.split(":")
            if len(parts) >= 2:
                host_path = os.path.expandvars(os.path.expanduser(parts[0]))
                container_path = parts[1]
                mode = parts[2] if len(parts) > 2 else "rw"

                # Ensure host directory exists if mount is writable
                if mode == "rw" and not os.path.exists(host_path):
                    try:
                        os.makedirs(host_path, mode=0o755, exist_ok=True)
                        logger.info(f"Created volume directory: {host_path}")
                    except OSError as e:
                        logger.warning(f"Could not create volume directory {host_path}: {e}")

                # CI helper: optionally relax permissions on writable bind mounts so
                # container processes can write (GitHub Actions runners can mount
                # workspaces with unexpected ownership/permissions).
                #
                # Opt-in via MCP_CI_RELAX_VOLUME_PERMS=true|1|yes
                # Only applies to rw mounts and only if host_path is a directory.
                relax = os.getenv("MCP_CI_RELAX_VOLUME_PERMS", "").strip().lower() in {
                    "1",
                    "true",
                    "yes",
                }
                if relax and mode == "rw":
                    try:
                        if os.path.isdir(host_path):
                            # Make directory traversable and writable for container UID/GID.
                            # 0o777 is intentionally permissive for CI stability; do not
                            # enable this in production environments.
                            os.chmod(host_path, 0o777)
                            logger.info(f"Relaxed volume permissions (chmod 777): {host_path}")
                    except OSError as e:
                        logger.warning(f"Could not relax volume permissions for {host_path}: {e}")

                cmd.extend(["-v", f"{host_path}:{container_path}:{mode}"])
            else:
                cmd.extend(["-v", volume])

        # Environment variables
        for key, value in config.env.items():
            sanitized = self._sanitizer.sanitize_environment_value(value)
            cmd.extend(["-e", f"{key}={sanitized}"])

        # Command override (entrypoint)
        if config.command:
            cmd.extend(["--entrypoint", config.command[0]])

        # Provider identity label for container discovery
        if config.provider_id:
            cmd.extend(["--label", f"mcp-hangar.provider-id={config.provider_id}"])

        # Image
        cmd.append(config.image)

        # Command arguments (after image)
        # If command has multiple parts, add remaining as args
        if config.command and len(config.command) > 1:
            cmd.extend(config.command[1:])

        # Additional args
        if config.args:
            cmd.extend(config.args)

        return cmd

    def launch(
        self,
        image: str,
        command: list[str] | None = None,
        args: list[str] | None = None,
        volumes: list[str] | None = None,
        env: dict[str, str] | None = None,
        memory_limit: str = "512m",
        cpu_limit: str = "1.0",
        network: str = "none",
        read_only: bool = True,
        user: str | None = None,
        provider_id: str | None = None,
    ) -> StdioClient:
        """
        Launch a container provider.

        Args:
            image: Container image name and tag
            command: Override container entrypoint
            args: Arguments passed to container command
            volumes: Volume mounts (host:container:mode)
            env: Environment variables
            memory_limit: Memory limit (e.g., "512m", "1g")
            cpu_limit: CPU limit (e.g., "1.0", "0.5")
            network: Network mode ("none", "bridge", "host")
            read_only: Mount root filesystem read-only
            user: User to run as (UID:GID or username)
            provider_id: Provider identity for container label injection

        Returns:
            StdioClient connected to the container

        Raises:
            ProviderStartError: If container fails to start
            ValidationError: If inputs fail validation
        """
        if not image:
            raise ValidationError(message="Container image is required", field="image")

        # Validate image
        self._validate_image(image)

        # Validate volumes
        volumes = volumes or []
        for volume in volumes:
            self._validate_volume(volume)

        # Validate environment
        env = env or {}
        if env:
            result = self._validator.validate_environment_variables(env)
            if not result.valid:
                errors = "; ".join(e.message for e in result.errors)
                raise ValidationError(message=f"Environment validation failed: {errors}", field="env")

        # Build config
        config = ContainerConfig(
            image=image,
            command=command,
            args=args,
            volumes=volumes,
            env=env,
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
            network=network,
            read_only=read_only,
            user=user,
            provider_id=provider_id,
        )

        # Build command
        cmd = self._build_command(config)

        # Log launch
        logger.info(f"Launching container [{self._runtime}]: {image}")
        logger.info(f"Container full command: {' '.join(cmd)}")

        try:
            inherit_stderr = os.getenv("MCP_CONTAINER_INHERIT_STDERR", "").strip().lower() in {
                "1",
                "true",
                "yes",
            }

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=(
                    None if inherit_stderr else subprocess.PIPE
                ),  # inherit when enabled; otherwise capture for debugging
                text=True,
                bufsize=1,
                shell=False,
            )
            return StdioClient(process)
        except FileNotFoundError as e:
            raise ProviderStartError(
                provider_id="unknown",
                reason=f"{self._runtime} not found. Is it installed and in PATH?",
                details={"image": image},
            ) from e
        except (OSError, subprocess.SubprocessError) as e:
            raise ProviderStartError(
                provider_id="unknown",
                reason=f"container_spawn_failed: {e}",
                details={"image": image, "runtime": self._runtime},
            ) from e

    def launch_with_config(self, config: ContainerConfig) -> StdioClient:
        """
        Launch a container with full configuration object.

        Args:
            config: Complete container configuration

        Returns:
            StdioClient connected to the container
        """
        return self.launch(
            image=config.image,
            command=config.command,
            args=config.args,
            volumes=config.volumes,
            env=config.env,
            memory_limit=config.memory_limit,
            cpu_limit=config.cpu_limit,
            network=config.network,
            read_only=config.read_only,
            user=config.user,
        )
