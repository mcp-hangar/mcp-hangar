"""Provider launcher interface and implementations.

Security-hardened launchers with:
- Input validation
- Command injection prevention
- Secure environment handling
- Audit logging

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

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import os
import shutil
import subprocess
import sys

from ...logging_config import get_logger
from ...stdio_client import StdioClient
from ..exceptions import ProviderStartError, ValidationError
from ..security.input_validator import InputValidator
from ..security.sanitizer import Sanitizer
from ..security.secrets import is_sensitive_key

logger = get_logger(__name__)


class ProviderLauncher(ABC):
    """
    Abstract interface for launching providers.

    This is a domain service interface that defines how providers are started.
    Implementations handle the specific infrastructure details (subprocess, docker, etc.)
    """

    @abstractmethod
    def launch(self, *args, **kwargs) -> StdioClient:
        """
        Launch a provider and return a connected client.

        Returns:
            StdioClient connected to the launched provider

        Raises:
            ProviderStartError: If the provider fails to start
            ValidationError: If inputs fail security validation
        """
        pass


class SubprocessLauncher(ProviderLauncher):
    """
    Launch providers as local subprocesses.

    This is the primary mode for running MCP providers locally.
    Security-hardened with:
    - Command validation
    - Argument sanitization
    - Environment filtering
    """

    # Default blocked executables
    DEFAULT_BLOCKED_COMMANDS: set[str] = {
        "rm",
        "rmdir",
        "del",
        "format",  # Destructive
        "sudo",
        "su",
        "doas",  # Privilege escalation
        "curl",
        "wget",
        "nc",
        "netcat",  # Network tools
        "bash",
        "sh",
        "zsh",
        "fish",
        "cmd",
        "powershell",  # Shells
        "eval",
        "exec",  # Dangerous builtins
    }

    # Allowed Python executables
    PYTHON_EXECUTABLES: set[str] = {
        "python",
        "python3",
        "python3.11",
        "python3.12",
        "python3.13",
        "python3.14",
    }

    def __init__(
        self,
        allowed_commands: set[str] | None = None,
        blocked_commands: set[str] | None = None,
        allow_absolute_paths: bool = True,
        inherit_env: bool = True,
        filter_sensitive_env: bool = True,
        env_whitelist: set[str] | None = None,
        env_blacklist: set[str] | None = None,
    ):
        """
        Initialize subprocess launcher with security configuration.

        Args:
            allowed_commands: Whitelist of allowed commands (if set, only these are allowed)
            blocked_commands: Blacklist of blocked commands
            allow_absolute_paths: Whether to allow absolute paths in commands
            inherit_env: Whether to inherit parent process environment
            filter_sensitive_env: Whether to filter sensitive env vars from inheritance
            env_whitelist: If set, only inherit these env vars
            env_blacklist: Env vars to never inherit
        """
        self._allowed_commands = allowed_commands
        self._blocked_commands = blocked_commands or self.DEFAULT_BLOCKED_COMMANDS
        self._allow_absolute_paths = allow_absolute_paths
        self._inherit_env = inherit_env
        self._filter_sensitive_env = filter_sensitive_env
        self._env_whitelist = env_whitelist
        self._env_blacklist = env_blacklist or {
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "GITHUB_TOKEN",
            "NPM_TOKEN",
        }

        # Create validator with our settings
        self._validator = InputValidator(
            allow_absolute_paths=allow_absolute_paths,
            allowed_commands=list(allowed_commands) if allowed_commands else None,
            blocked_commands=list(self._blocked_commands),
        )

        self._sanitizer = Sanitizer()

    def _validate_command(self, command: list[str]) -> None:
        """
        Validate and security-check the command.

        Raises:
            ValidationError: If command fails validation
        """
        result = self._validator.validate_command(command)

        if not result.valid:
            errors = "; ".join(e.message for e in result.errors)
            logger.warning(f"Command validation failed: {errors}")
            raise ValidationError(
                message=f"Command validation failed: {errors}",
                field="command",
                details={"errors": [e.to_dict() for e in result.errors]},
            )

        # Additional security checks
        if command:
            executable = os.path.basename(command[0])

            # Always allow Python (needed for MCP providers)
            if executable not in self.PYTHON_EXECUTABLES:
                # Check explicit blocklist
                if executable in self._blocked_commands:
                    logger.warning(f"Blocked command attempted: {executable}")
                    raise ValidationError(
                        message=f"Command '{executable}' is not allowed",
                        field="command[0]",
                        value=executable,
                    )

                # Check allowlist if configured
                if self._allowed_commands is not None:
                    if executable not in self._allowed_commands:
                        raise ValidationError(
                            message=f"Command '{executable}' is not in the allowed list",
                            field="command[0]",
                            value=executable,
                        )

    def _validate_env(self, env: dict[str, str] | None) -> None:
        """
        Validate environment variables.

        Raises:
            ValidationError: If env vars fail validation
        """
        if env is None:
            return

        result = self._validator.validate_environment_variables(env)

        if not result.valid:
            errors = "; ".join(e.message for e in result.errors)
            raise ValidationError(
                message=f"Environment validation failed: {errors}",
                field="env",
                details={"errors": [e.to_dict() for e in result.errors]},
            )

    def _prepare_env(self, provider_env: dict[str, str] | None = None) -> dict[str, str]:
        """
        Prepare secure environment for subprocess.

        Args:
            provider_env: Provider-specific environment variables

        Returns:
            Sanitized environment dictionary
        """
        result_env: dict[str, str] = {}

        # Start with inherited env if configured
        if self._inherit_env:
            for key, value in os.environ.items():
                # Apply whitelist
                if self._env_whitelist is not None:
                    if key not in self._env_whitelist:
                        continue

                # Apply blacklist
                if self._env_blacklist and key in self._env_blacklist:
                    continue

                # Filter sensitive env vars
                if self._filter_sensitive_env and is_sensitive_key(key):
                    continue

                result_env[key] = value

        # Add provider-specific env vars (overrides inherited)
        if provider_env:
            # Sanitize values
            for key, value in provider_env.items():
                sanitized = self._sanitizer.sanitize_environment_value(value)
                result_env[key] = sanitized

        return result_env

    def launch(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
    ) -> StdioClient:
        """
        Launch a subprocess provider with security validation.

        Args:
            command: Command and arguments to execute
            env: Additional environment variables

        Returns:
            StdioClient connected to the subprocess

        Raises:
            ProviderStartError: If subprocess fails to start
            ValidationError: If inputs fail security validation
        """
        if not command:
            raise ValidationError(message="Command is required", field="command")

        # Validate command
        self._validate_command(command)

        # Validate environment
        self._validate_env(env)

        # Prepare secure environment
        process_env = self._prepare_env(env)

        # Resolve interpreter robustly (tests often pass "python" which may not exist on macOS)
        resolved_command = list(command)
        head = resolved_command[0] if resolved_command else ""
        if head in ("python", "python3"):
            resolved = shutil.which(head)
            if not resolved:
                # Prefer the current interpreter if available; it's the safest default in this process
                if sys.executable:
                    resolved = sys.executable
            if resolved:
                resolved_command[0] = resolved

        # Log launch (without sensitive data)
        safe_command = [c[:50] + "..." if len(c) > 50 else c for c in resolved_command[:5]]
        logger.info(f"Launching subprocess: {safe_command}")

        try:
            process = subprocess.Popen(
                resolved_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # Capture stderr for error diagnostics
                text=True,
                env=process_env,
                bufsize=1,  # Line buffered
                # Security: Don't use shell
                shell=False,
            )
            return StdioClient(process)
        except FileNotFoundError as e:
            raise ProviderStartError(
                provider_id="unknown",
                reason=f"Command not found: {resolved_command[0] if resolved_command else ''}",
                details={"command": safe_command},
            ) from e
        except PermissionError as e:
            raise ProviderStartError(
                provider_id="unknown",
                reason=f"Permission denied: {resolved_command[0] if resolved_command else ''}",
                details={"command": safe_command},
            ) from e
        except Exception as e:
            raise ProviderStartError(
                provider_id="unknown",
                reason=f"subprocess_spawn_failed: {e}",
                details={"command": safe_command},
            ) from e


class DockerLauncher(ProviderLauncher):
    """
    Launch providers in Docker containers.

    Runs the provider image with stdin/stdout attached for MCP communication.
    Security-hardened with:
    - Image name validation
    - Environment sanitization
    - Resource limits
    - Network restrictions
    """

    # Docker images that are always blocked
    BLOCKED_IMAGES: set[str] = {
        "ubuntu",
        "debian",
        "alpine",
        "busybox",  # Base images (too generic)
    }

    def __init__(
        self,
        allowed_registries: set[str] | None = None,
        blocked_images: set[str] | None = None,
        enable_network: bool = False,
        memory_limit: str = "512m",
        cpu_limit: str = "1.0",
        read_only: bool = True,
        drop_capabilities: bool = True,
        runtime: str | None = None,  # "docker", "podman", or None for auto-detect
    ):
        """
        Initialize Docker launcher with security configuration.

        Args:
            allowed_registries: Whitelist of allowed registries (e.g., {"ghcr.io", "docker.io"})
            blocked_images: Images that cannot be run
            enable_network: Whether to allow network access in container
            memory_limit: Memory limit for container
            cpu_limit: CPU limit for container
            read_only: Whether to mount filesystem read-only
            drop_capabilities: Whether to drop all capabilities
            runtime: Container runtime ("docker", "podman", or None for auto-detect)
        """
        self._allowed_registries = allowed_registries
        self._blocked_images = blocked_images or self.BLOCKED_IMAGES
        self._enable_network = enable_network
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._read_only = read_only
        self._drop_capabilities = drop_capabilities
        self._runtime = runtime or self._detect_runtime()

        self._validator = InputValidator()
        self._sanitizer = Sanitizer()

    def _detect_runtime(self) -> str:
        """Auto-detect container runtime (docker or podman)."""
        import shutil

        # Check for podman first (preferred on macOS with Podman Desktop)
        if shutil.which("podman"):
            logger.debug("Detected container runtime: podman")
            return "podman"

        if shutil.which("docker"):
            logger.debug("Detected container runtime: docker")
            return "docker"

        # Default to docker, will fail at runtime if not available
        logger.warning("No container runtime found in PATH, defaulting to 'docker'")
        return "docker"

    def _validate_image(self, image: str) -> None:
        """
        Validate Docker image name.

        Raises:
            ValidationError: If image fails validation
        """
        result = self._validator.validate_docker_image(image)

        if not result.valid:
            errors = "; ".join(e.message for e in result.errors)
            raise ValidationError(
                message=f"Docker image validation failed: {errors}",
                field="image",
                value=image,
            )

        # Check blocked images
        image_name = image.split(":")[0].split("/")[-1]
        if image_name in self._blocked_images:
            raise ValidationError(
                message=f"Docker image '{image_name}' is not allowed",
                field="image",
                value=image,
            )

        # Check registry whitelist
        if self._allowed_registries:
            # Extract registry from image name
            parts = image.split("/")
            if len(parts) > 1 and "." in parts[0]:
                registry = parts[0]
            else:
                registry = "docker.io"  # Default registry

            if registry not in self._allowed_registries:
                raise ValidationError(
                    message=f"Registry '{registry}' is not in the allowed list",
                    field="image",
                    value=image,
                )

    def _build_docker_command(
        self,
        image: str,
        env: dict[str, str] | None = None,
    ) -> list[str]:
        """
        Build secure Docker/Podman run command.

        Args:
            image: Docker image to run
            env: Environment variables for container

        Returns:
            Complete container run command as list
        """
        cmd = [self._runtime, "run", "--rm", "-i"]

        # Security options
        if not self._enable_network:
            cmd.extend(["--network", "none"])

        if self._memory_limit:
            cmd.extend(["--memory", self._memory_limit])

        if self._cpu_limit:
            cmd.extend(["--cpus", self._cpu_limit])

        if self._read_only:
            cmd.append("--read-only")

        if self._drop_capabilities:
            cmd.extend(["--cap-drop", "ALL"])

        # Add user namespace remapping for security
        cmd.extend(["--security-opt", "no-new-privileges"])

        # Add environment variables (sanitized)
        if env:
            for key, value in env.items():
                # Sanitize value
                sanitized = self._sanitizer.sanitize_environment_value(value)
                # Docker -e format
                cmd.extend(["-e", f"{key}={sanitized}"])

        # Image name
        cmd.append(image)

        return cmd

    def launch(
        self,
        image: str,
        env: dict[str, str] | None = None,
    ) -> StdioClient:
        """
        Launch a Docker provider with security validation.

        Args:
            image: Docker image name and tag
            env: Environment variables to pass to container

        Returns:
            StdioClient connected to the Docker container

        Raises:
            ProviderStartError: If container fails to start
            ValidationError: If inputs fail security validation
        """
        if not image:
            raise ValidationError(message="Docker image is required", field="image")

        # Validate image
        self._validate_image(image)

        # Validate environment
        if env:
            result = self._validator.validate_environment_variables(env)
            if not result.valid:
                errors = "; ".join(e.message for e in result.errors)
                raise ValidationError(message=f"Environment validation failed: {errors}", field="env")

        # Build secure command
        cmd = self._build_docker_command(image, env)

        # Log launch
        logger.info(f"Launching Docker container: {image}")

        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                shell=False,  # Never use shell
            )
            return StdioClient(process)
        except FileNotFoundError as e:
            raise ProviderStartError(
                provider_id="unknown",
                reason="Docker not found. Is Docker installed and in PATH?",
                details={"image": image},
            ) from e
        except Exception as e:
            raise ProviderStartError(
                provider_id="unknown",
                reason=f"docker_spawn_failed: {e}",
                details={"image": image},
            ) from e


@dataclass
class ContainerConfig:
    """Configuration for container-based provider launch."""

    image: str
    volumes: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    memory_limit: str = "512m"
    cpu_limit: str = "1.0"
    network: str = "none"  # none, bridge, host
    read_only: bool = True
    drop_capabilities: bool = False  # Disabled: causes issues with native modules (e.g., better-sqlite3)
    user: str | None = None  # Run as specific user


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

        # Image
        cmd.append(config.image)

        return cmd

    def launch(
        self,
        image: str,
        volumes: list[str] | None = None,
        env: dict[str, str] | None = None,
        memory_limit: str = "512m",
        cpu_limit: str = "1.0",
        network: str = "none",
        read_only: bool = True,
        user: str | None = None,
    ) -> StdioClient:
        """
        Launch a container provider.

        Args:
            image: Container image name and tag
            volumes: Volume mounts (host:container:mode)
            env: Environment variables
            memory_limit: Memory limit (e.g., "512m", "1g")
            cpu_limit: CPU limit (e.g., "1.0", "0.5")
            network: Network mode ("none", "bridge", "host")
            read_only: Mount root filesystem read-only
            user: User to run as (UID:GID or username)

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
            volumes=volumes,
            env=env,
            memory_limit=memory_limit,
            cpu_limit=cpu_limit,
            network=network,
            read_only=read_only,
            user=user,
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
        except Exception as e:
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
            volumes=config.volumes,
            env=config.env,
            memory_limit=config.memory_limit,
            cpu_limit=config.cpu_limit,
            network=config.network,
            read_only=config.read_only,
        )


# --- HTTP Launcher ---


class HttpLauncher(ProviderLauncher):
    """
    Launcher for remote HTTP-based MCP providers.

    Connects to MCP providers exposed via HTTP/HTTPS endpoints.
    Supports:
    - Multiple authentication schemes (none, API key, bearer token, basic)
    - SSE (Server-Sent Events) streaming
    - TLS with custom CA certificates
    - Connection pooling and retry logic

    Note: This launcher does not start a process - it creates a client
    that connects to an already-running remote provider.
    """

    def __init__(
        self,
        verify_ssl: bool = True,
        ca_cert_path: str | None = None,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        max_retries: int = 3,
    ):
        """
        Initialize HTTP launcher with default configuration.

        Args:
            verify_ssl: Whether to verify SSL certificates.
            ca_cert_path: Path to custom CA certificate file.
            connect_timeout: Default connection timeout in seconds.
            read_timeout: Default read timeout in seconds.
            max_retries: Default maximum retry attempts.
        """
        self._verify_ssl = verify_ssl
        self._ca_cert_path = ca_cert_path
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._max_retries = max_retries

        self._validator = InputValidator()

    def _validate_endpoint(self, endpoint: str) -> None:
        """
        Validate HTTP endpoint URL.

        Raises:
            ValidationError: If endpoint is invalid.
        """
        if not endpoint:
            raise ValidationError(message="Endpoint is required", field="endpoint")

        from urllib.parse import urlparse

        parsed = urlparse(endpoint)

        if not parsed.scheme:
            raise ValidationError(
                message="Endpoint must include scheme (http or https)",
                field="endpoint",
                value=endpoint,
            )

        if parsed.scheme not in ("http", "https"):
            raise ValidationError(
                message=f"Unsupported endpoint scheme: {parsed.scheme}. Use http or https.",
                field="endpoint",
                value=endpoint,
            )

        if not parsed.netloc:
            raise ValidationError(
                message="Endpoint must include host",
                field="endpoint",
                value=endpoint,
            )

    def launch(
        self,
        endpoint: str,
        auth_config: dict | None = None,
        tls_config: dict | None = None,
        http_config: dict | None = None,
    ):
        """
        Create an HTTP client for a remote MCP provider.

        Args:
            endpoint: HTTP/HTTPS URL of the MCP provider.
            auth_config: Authentication configuration dict.
            tls_config: TLS configuration dict.
            http_config: HTTP transport configuration dict.

        Returns:
            HttpClient connected to the remote provider.

        Raises:
            ValidationError: If inputs fail validation.
            ProviderStartError: If connection cannot be established.
        """
        # Validate endpoint
        self._validate_endpoint(endpoint)

        # Import here to avoid circular imports
        from ...http_client import AuthConfig, AuthType, HttpClient, HttpClientConfig

        # Build auth config
        auth = AuthConfig()
        if auth_config:
            auth_type_str = auth_config.get("type", "none")
            try:
                auth_type = AuthType(auth_type_str)
            except ValueError as e:
                raise ValidationError(
                    message=f"Invalid auth type: {auth_type_str}. Use: none, api_key, bearer, basic.",
                    field="auth.type",
                    value=auth_type_str,
                ) from e

            auth = AuthConfig(
                auth_type=auth_type,
                api_key=auth_config.get("api_key"),
                api_key_header=auth_config.get("api_key_header", "X-API-Key"),
                bearer_token=auth_config.get("bearer_token"),
                basic_username=auth_config.get("username"),
                basic_password=auth_config.get("password"),
            )

        # Build HTTP client config
        http_cfg = HttpClientConfig(
            connect_timeout=self._connect_timeout,
            read_timeout=self._read_timeout,
            max_retries=self._max_retries,
            verify_ssl=self._verify_ssl,
            ca_cert_path=self._ca_cert_path,
        )

        if tls_config:
            http_cfg = HttpClientConfig(
                connect_timeout=http_cfg.connect_timeout,
                read_timeout=http_cfg.read_timeout,
                max_retries=http_cfg.max_retries,
                verify_ssl=tls_config.get("verify_ssl", True),
                ca_cert_path=tls_config.get("ca_cert_path"),
            )

        if http_config:
            http_cfg = HttpClientConfig(
                connect_timeout=http_config.get("connect_timeout", http_cfg.connect_timeout),
                read_timeout=http_config.get("read_timeout", http_cfg.read_timeout),
                max_retries=http_config.get("max_retries", http_cfg.max_retries),
                retry_backoff_factor=http_config.get("retry_backoff_factor", 0.5),
                verify_ssl=http_cfg.verify_ssl,
                ca_cert_path=http_cfg.ca_cert_path,
                extra_headers=http_config.get("headers", {}),
            )

        logger.info(
            f"Connecting to HTTP provider: {endpoint}",
            auth_type=auth.auth_type.value,
            verify_ssl=http_cfg.verify_ssl,
        )

        try:
            client = HttpClient(
                endpoint=endpoint,
                auth_config=auth,
                http_config=http_cfg,
            )
            return client
        except Exception as e:
            raise ProviderStartError(
                provider_id="unknown",
                reason=f"Failed to connect to HTTP provider: {e}",
                details={"endpoint": endpoint},
            ) from e


# --- Factory Function ---


def get_launcher(mode: str) -> ProviderLauncher:
    """
    Factory function to get the appropriate launcher for a mode.

    Args:
        mode: Provider mode (subprocess, docker, container, podman, remote)

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

    return launcher_factory() if callable(launcher_factory) else launcher_factory
