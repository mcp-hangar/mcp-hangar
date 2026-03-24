"""Docker provider launcher implementation."""

import shutil
import subprocess

from ....logging_config import get_logger
from ....stdio_client import StdioClient
from ...exceptions import ProviderStartError, ValidationError
from ...security.input_validator import InputValidator
from ...security.sanitizer import Sanitizer
from ...value_objects.capabilities import ProviderCapabilities
from .base import ProviderLauncher

logger = get_logger(__name__)


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
        capabilities: ProviderCapabilities | None = None,
    ) -> list[str]:
        """
        Build secure Docker/Podman run command.

        When ``capabilities`` is provided, its ``network.egress`` list drives network
        mode selection, overriding the legacy ``enable_network`` constructor flag:

        * Empty egress (no declared destinations) -> ``--network none`` (deny all).
        * Non-empty egress (at least one destination) -> default bridge network.

        Note: Docker provides *binary* enforcement only (deny-all or allow-all).
        Unlike Kubernetes NetworkPolicy, Docker ``--network`` flags cannot restrict
        traffic to specific CIDRs or ports. Per-destination filtering requires K8s.

        Args:
            image: Docker image to run
            env: Environment variables for container
            capabilities: Optional provider capability declaration. When present,
                network.egress drives network mode selection.

        Returns:
            Complete container run command as list
        """
        cmd = [self._runtime, "run", "--rm", "-i"]

        # Network isolation: capabilities override the legacy enable_network flag
        network_deny = False
        if capabilities is not None and capabilities.network is not None:
            # Capabilities-driven: deny if no egress destinations declared
            if not capabilities.network.egress:
                network_deny = True
            # else: egress declared -> allow network (bridge)
        elif not self._enable_network:
            # Legacy fallback: use constructor flag
            network_deny = True

        if network_deny:
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
        capabilities: ProviderCapabilities | None = None,
    ) -> StdioClient:
        """
        Launch a Docker provider with security validation.

        Args:
            image: Docker image name and tag
            env: Environment variables to pass to container
            capabilities: Optional provider capability declaration. When present,
                network.egress drives network mode (overrides enable_network flag).

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
        cmd = self._build_docker_command(image, env, capabilities=capabilities)

        # Log launch
        logger.info(f"Launching Docker container: {image}")

        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # Capture stderr for live log streaming
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
        except (OSError, subprocess.SubprocessError) as e:
            raise ProviderStartError(
                provider_id="unknown",
                reason=f"docker_spawn_failed: {e}",
                details={"image": image},
            ) from e
