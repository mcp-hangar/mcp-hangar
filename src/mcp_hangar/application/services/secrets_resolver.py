"""Secrets resolver for MCP provider environment variables.

This module provides functionality to resolve required secrets for MCP providers
from environment variables and configuration files.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path

from ...logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SecretsResult:
    """Result of secrets resolution.

    Attributes:
        resolved: Dictionary mapping secret names to their values.
        missing: List of secret names that could not be resolved.
        sources: Dictionary mapping secret names to their source ("env" or "file").
    """

    resolved: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def all_resolved(self) -> bool:
        """Check if all required secrets were resolved."""
        return len(self.missing) == 0


class SecretsResolver:
    """Resolves secrets for MCP providers from environment and files.

    Resolution order:
    1. Environment variable (exact name)
    2. File: ~/.config/mcp-hangar/secrets/{provider}/{VAR_NAME}
    3. File: ~/.config/mcp-hangar/secrets/{VAR_NAME}

    Attributes:
        secrets_dir: Base directory for secrets files.
    """

    DEFAULT_SECRETS_DIR = Path.home() / ".config" / "mcp-hangar" / "secrets"

    def __init__(self, secrets_dir: Path | None = None):
        """Initialize the secrets resolver.

        Args:
            secrets_dir: Optional custom secrets directory.
        """
        self._secrets_dir = secrets_dir or self.DEFAULT_SECRETS_DIR

    @property
    def secrets_dir(self) -> Path:
        """Get the secrets directory."""
        return self._secrets_dir

    def resolve(self, required: list[str], provider_name: str) -> SecretsResult:
        """Resolve required secrets for a provider.

        Args:
            required: List of required secret names (environment variable names).
            provider_name: Name of the provider (used for provider-specific secrets).

        Returns:
            SecretsResult with resolved secrets and any missing ones.
        """
        result = SecretsResult()

        for secret_name in required:
            value, source = self._resolve_single(secret_name, provider_name)
            if value is not None:
                result.resolved[secret_name] = value
                result.sources[secret_name] = source
                logger.debug(
                    "secret_resolved",
                    secret_name=secret_name,
                    provider=provider_name,
                    source=source,
                )
            else:
                result.missing.append(secret_name)
                logger.debug(
                    "secret_not_found",
                    secret_name=secret_name,
                    provider=provider_name,
                )

        return result

    def _resolve_single(self, secret_name: str, provider_name: str) -> tuple[str | None, str]:
        """Resolve a single secret.

        Args:
            secret_name: Name of the secret to resolve.
            provider_name: Name of the provider.

        Returns:
            Tuple of (value, source) or (None, "") if not found.
        """
        value = os.environ.get(secret_name)
        if value is not None:
            return value, "env"

        provider_file = self._secrets_dir / provider_name / secret_name
        if provider_file.is_file():
            try:
                value = provider_file.read_text().strip()
                return value, "file"
            except (OSError, PermissionError) as e:
                logger.warning(
                    "secret_file_read_error",
                    path=str(provider_file),
                    error=str(e),
                )

        global_file = self._secrets_dir / secret_name
        if global_file.is_file():
            try:
                value = global_file.read_text().strip()
                return value, "file"
            except (OSError, PermissionError) as e:
                logger.warning(
                    "secret_file_read_error",
                    path=str(global_file),
                    error=str(e),
                )

        return None, ""

    def get_missing_instructions(self, missing: list[str], provider_name: str) -> str:
        """Generate instructions for setting up missing secrets.

        Args:
            missing: List of missing secret names.
            provider_name: Name of the provider.

        Returns:
            Human-readable instructions for setting up the secrets.
        """
        lines = [
            f"The following secrets are required for '{provider_name}':",
            "",
        ]

        for secret in missing:
            lines.append(f"  - {secret}")

        lines.extend(
            [
                "",
                "You can provide these secrets in one of the following ways:",
                "",
                "1. Environment variables:",
            ]
        )

        for secret in missing:
            lines.append(f"   export {secret}=your_value")

        lines.extend(
            [
                "",
                f"2. Files in ~/.config/mcp-hangar/secrets/{provider_name}/:",
            ]
        )

        for secret in missing:
            lines.append(f"   echo 'your_value' > ~/.config/mcp-hangar/secrets/{provider_name}/{secret}")

        lines.extend(
            [
                "",
                "3. Global files in ~/.config/mcp-hangar/secrets/:",
            ]
        )

        for secret in missing:
            lines.append(f"   echo 'your_value' > ~/.config/mcp-hangar/secrets/{secret}")

        return "\n".join(lines)
