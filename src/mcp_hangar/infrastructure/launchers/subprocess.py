"""Subprocess provider launcher implementation."""

import os
import shutil
import subprocess
import sys

from mcp_hangar.logging_config import get_logger
from mcp_hangar.stdio_client import StdioClient
from mcp_hangar.domain.exceptions import ProviderStartError, ValidationError
from mcp_hangar.domain.security.input_validator import InputValidator
from mcp_hangar.domain.security.sanitizer import Sanitizer
from mcp_hangar.domain.security.secrets import is_sensitive_key
from .base import ProviderLauncher

logger = get_logger(__name__)


class SubprocessLauncher(ProviderLauncher):
    """
    Launch providers as local subprocesses.

    This is the primary mode for running MCP providers locally.
    Security-hardened with:
    - Command validation
    - Argument sanitization
    - Environment filtering
    """

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
            allowed_commands: Allowed commands override. Defaults to InputValidator allow-list.
            blocked_commands: Deprecated compatibility argument. Ignored.
            allow_absolute_paths: Whether to allow absolute paths in commands
            inherit_env: Whether to inherit parent process environment
            filter_sensitive_env: Whether to filter sensitive env vars from inheritance
            env_whitelist: If set, only inherit these env vars
            env_blacklist: Env vars to never inherit
        """
        self._allowed_commands = allowed_commands
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
        )

        self._sanitizer = Sanitizer()

    def _validate_command(self, command: list[str]) -> None:
        """
        Validate and security-check the command.

        Raises:
            ValidationError: If command fails validation
        """
        try:
            result = self._validator.validate_command(command)
        except ValueError as exc:
            logger.warning(f"Command rejected by allow-list: {exc}")
            raise ValueError(str(exc)) from exc

        if not result.valid:
            errors = "; ".join(e.message for e in result.errors)
            logger.warning(f"Command validation failed: {errors}")
            raise ValidationError(
                message=f"Command validation failed: {errors}",
                field="command",
                details={"errors": [e.to_dict() for e in result.errors]},
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
        except (OSError, subprocess.SubprocessError) as e:
            raise ProviderStartError(
                provider_id="unknown",
                reason=f"subprocess_spawn_failed: {e}",
                details={"command": safe_command},
            ) from e
