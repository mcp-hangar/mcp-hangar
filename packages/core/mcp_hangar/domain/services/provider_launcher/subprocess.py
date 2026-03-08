"""Subprocess provider launcher implementation."""

import os
import shutil
import subprocess
import sys

from ....logging_config import get_logger
from ....stdio_client import StdioClient
from ...exceptions import ProviderStartError, ValidationError
from ...security.input_validator import InputValidator
from ...security.sanitizer import Sanitizer
from ...security.secrets import is_sensitive_key
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
        except (OSError, subprocess.SubprocessError) as e:
            raise ProviderStartError(
                provider_id="unknown",
                reason=f"subprocess_spawn_failed: {e}",
                details={"command": safe_command},
            ) from e
