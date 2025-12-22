"""Image builder for Docker/Podman containers."""

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Optional

from ..exceptions import ProviderStartError

logger = logging.getLogger(__name__)


@dataclass
class BuildConfig:
    """Configuration for building a container image."""

    dockerfile: str
    context: str = "."
    tag: Optional[str] = None
    build_args: Optional[dict] = None


class ImageBuilder:
    """
    Build Docker/Podman images on demand.

    Features:
    - Auto-detect container runtime (podman > docker)
    - Build images from Dockerfile
    - Cache check - skip build if image exists
    - Generate deterministic tags based on Dockerfile hash
    """

    def __init__(self, runtime: str = "auto", base_path: Optional[str] = None):
        """
        Initialize image builder.

        Args:
            runtime: Container runtime ("auto", "podman", "docker")
            base_path: Base path for resolving relative Dockerfile paths
        """
        self._runtime = self._detect_runtime(runtime)
        self._base_path = Path(base_path) if base_path else Path.cwd()

        logger.info(f"ImageBuilder initialized with runtime: {self._runtime}")

    @property
    def runtime(self) -> str:
        """Get the container runtime being used."""
        return self._runtime

    def _detect_runtime(self, preference: str) -> str:
        """
        Detect available container runtime.

        Prefers podman over docker (rootless by default = more secure).

        Args:
            preference: "auto", "podman", or "docker"

        Returns:
            Detected runtime name (full path if needed)

        Raises:
            ProviderStartError: If no runtime found
        """
        runtime_path = self._find_runtime(preference)
        if runtime_path:
            return runtime_path

        if preference != "auto":
            raise ProviderStartError(
                provider_id="image_builder",
                reason=f"Container runtime '{preference}' not found in PATH",
            )

        raise ProviderStartError(
            provider_id="image_builder",
            reason="No container runtime found. Install podman or docker.",
        )

    def _find_runtime(self, preference: str) -> Optional[str]:
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

    def _resolve_path(self, path: str) -> Path:
        """Resolve a path relative to base_path."""
        p = Path(path)
        if p.is_absolute():
            return p
        return self._base_path / p

    def _generate_tag(self, config: BuildConfig) -> str:
        """
        Generate a deterministic image tag based on Dockerfile content.

        Args:
            config: Build configuration

        Returns:
            Image tag like "mcp-filesystem:a1b2c3d4"
        """
        if config.tag:
            return config.tag

        dockerfile_path = self._resolve_path(config.dockerfile)

        # Hash the Dockerfile content for cache invalidation
        try:
            content = dockerfile_path.read_bytes()
            content_hash = hashlib.sha256(content).hexdigest()[:8]
        except FileNotFoundError:
            content_hash = "unknown"

        # Extract name from Dockerfile path (e.g., "Dockerfile.filesystem" -> "filesystem")
        filename = dockerfile_path.name  # e.g., "Dockerfile.filesystem"
        if filename.startswith("Dockerfile."):
            # Dockerfile.memory -> memory
            name = filename.replace("Dockerfile.", "")
        elif filename == "Dockerfile":
            # Use parent directory name
            name = dockerfile_path.parent.name
        else:
            # Fallback to stem
            name = dockerfile_path.stem

        return f"mcp-{name}:{content_hash}"

    def image_exists(self, tag: str) -> bool:
        """
        Check if an image with the given tag exists.

        Args:
            tag: Image tag to check

        Returns:
            True if image exists
        """
        try:
            result = subprocess.run(
                [self._runtime, "image", "inspect", tag],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"Failed to check image existence: {e}")
            return False

    def build(self, config: BuildConfig, force: bool = False) -> str:
        """
        Build an image from Dockerfile.

        Args:
            config: Build configuration
            force: Force rebuild even if image exists

        Returns:
            Image tag

        Raises:
            ProviderStartError: If build fails
        """
        tag = self._generate_tag(config)

        # Check if already exists
        if not force and self.image_exists(tag):
            logger.info(f"Image {tag} already exists, skipping build")
            return tag

        dockerfile_path = self._resolve_path(config.dockerfile)
        context_path = self._resolve_path(config.context)

        # Validate paths
        if not dockerfile_path.exists():
            raise ProviderStartError(
                provider_id="image_builder",
                reason=f"Dockerfile not found: {dockerfile_path}",
            )

        if not context_path.exists():
            raise ProviderStartError(
                provider_id="image_builder",
                reason=f"Build context not found: {context_path}",
            )

        # Build command
        cmd = [
            self._runtime,
            "build",
            "-t",
            tag,
            "-f",
            str(dockerfile_path),
        ]

        # Add build args
        if config.build_args:
            for key, value in config.build_args.items():
                cmd.extend(["--build-arg", f"{key}={value}"])

        # Add context
        cmd.append(str(context_path))

        logger.info(f"Building image {tag} from {dockerfile_path}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout for builds
                cwd=str(self._base_path),
            )

            if result.returncode != 0:
                logger.error(f"Build failed: {result.stderr}")
                raise ProviderStartError(
                    provider_id="image_builder",
                    reason=f"Image build failed: {result.stderr[:500]}",
                )

            logger.info(f"Successfully built image {tag}")
            return tag

        except subprocess.TimeoutExpired:
            raise ProviderStartError(
                provider_id="image_builder",
                reason="Image build timed out after 10 minutes",
            )
        except Exception as e:
            raise ProviderStartError(provider_id="image_builder", reason=f"Image build failed: {e}")

    def build_if_needed(self, config: BuildConfig) -> str:
        """
        Build image only if it doesn't exist.

        Convenience method that combines tag generation and conditional build.

        Args:
            config: Build configuration

        Returns:
            Image tag (either existing or newly built)
        """
        return self.build(config, force=False)

    def remove_image(self, tag: str) -> bool:
        """
        Remove an image.

        Args:
            tag: Image tag to remove

        Returns:
            True if removed successfully
        """
        try:
            result = subprocess.run([self._runtime, "rmi", tag], capture_output=True, text=True, timeout=60)
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"Failed to remove image {tag}: {e}")
            return False


# Singleton instance
_builder_instance: Optional[ImageBuilder] = None


def get_image_builder(runtime: str = "auto", base_path: Optional[str] = None) -> ImageBuilder:
    """
    Get or create the ImageBuilder singleton.

    Args:
        runtime: Container runtime preference
        base_path: Base path for Dockerfile resolution

    Returns:
        ImageBuilder instance
    """
    global _builder_instance

    # Allow CI / operators to force a specific runtime.
    # Useful for stabilizing environments where both podman and docker exist.
    forced_runtime = os.getenv("MCP_CONTAINER_RUNTIME")
    if forced_runtime:
        runtime = forced_runtime.strip().lower()

    if _builder_instance is None:
        _builder_instance = ImageBuilder(runtime=runtime, base_path=base_path)

    return _builder_instance
