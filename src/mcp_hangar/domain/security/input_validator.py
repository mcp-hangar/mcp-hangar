"""
Input validation for MCP Hangar.

Provides comprehensive validation for all inputs at API boundaries.
Validation happens early to prevent invalid data from propagating through the system.
"""

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any


class ValidationSeverity(Enum):
    """Severity level for validation issues."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationIssue:
    """A single validation issue."""

    field: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR
    value: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "field": self.field,
            "message": self.message,
            "severity": self.severity.value,
        }
        if self.value is not None:
            # Truncate long values
            str_val = str(self.value)
            if len(str_val) > 100:
                str_val = str_val[:100] + "..."
            result["value"] = str_val
        return result


@dataclass
class ValidationResult:
    """Result of validation operation."""

    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def add_error(self, field: str, message: str, value: Any = None) -> None:
        """Add an error issue."""
        self.issues.append(
            ValidationIssue(
                field=field,
                message=message,
                severity=ValidationSeverity.ERROR,
                value=value,
            )
        )
        self.valid = False

    def add_warning(self, field: str, message: str, value: Any = None) -> None:
        """Add a warning issue."""
        self.issues.append(
            ValidationIssue(
                field=field,
                message=message,
                severity=ValidationSeverity.WARNING,
                value=value,
            )
        )

    def merge(self, other: "ValidationResult") -> None:
        """Merge another validation result into this one."""
        self.issues.extend(other.issues)
        if not other.valid:
            self.valid = False

    @property
    def errors(self) -> list[ValidationIssue]:
        """Get only error issues."""
        return [i for i in self.issues if i.severity == ValidationSeverity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        """Get only warning issues."""
        return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "valid": self.valid,
            "issues": [i.to_dict() for i in self.issues],
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
        }


# --- Validation Patterns ---

# Provider ID: alphanumeric, hyphens, underscores, 1-64 chars
PROVIDER_ID_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")

# Tool name: alphanumeric, underscores, dots, slashes (for namespacing)
TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_./-]{0,127}$")

# Docker image: standard docker image pattern
DOCKER_IMAGE_PATTERN = re.compile(
    r"^(?:(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)*[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?::[0-9]+)?/)?"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"(?::[a-zA-Z0-9][a-zA-Z0-9._-]{0,127})?"
    r"(?:@sha256:[a-fA-F0-9]{64})?$"
)

# Environment variable key: standard env var pattern
ENV_KEY_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Dangerous command patterns (potential injection)
DANGEROUS_PATTERNS = [
    re.compile(r";\s*"),  # Command chaining
    re.compile(r"\|\s*"),  # Pipe
    re.compile(r"`"),  # Backtick execution
    re.compile(r"\$\("),  # Command substitution
    re.compile(r"\$\{"),  # Variable expansion
    re.compile(r"&&"),  # AND chaining
    re.compile(r"\|\|"),  # OR chaining
    re.compile(r">\s*"),  # Redirect
    re.compile(r"<\s*"),  # Input redirect
    re.compile(r"\n"),  # Newline injection
    re.compile(r"\r"),  # Carriage return injection
    re.compile(r"\x00"),  # Null byte injection
]

# Dangerous path patterns
DANGEROUS_PATH_PATTERNS = [
    re.compile(r"\.\."),  # Path traversal
    re.compile(r"^/"),  # Absolute path (in some contexts)
    re.compile(r"~"),  # Home directory expansion
]


class InputValidator:
    """
    Comprehensive input validator for MCP Hangar.

    Validates all inputs at API boundaries to prevent:
    - Injection attacks
    - Buffer overflow attempts
    - Invalid data from propagating through the system
    """

    # Configurable limits
    MAX_PROVIDER_ID_LENGTH = 64
    MAX_TOOL_NAME_LENGTH = 128
    MAX_ARGUMENT_SIZE_BYTES = 1_000_000  # 1MB
    MAX_ARGUMENT_DEPTH = 10
    MAX_COMMAND_ARGS = 100
    MAX_ENV_VARS = 100
    MAX_ENV_KEY_LENGTH = 256
    MAX_ENV_VALUE_LENGTH = 32_768  # 32KB
    MIN_TIMEOUT = 0.1
    MAX_TIMEOUT = 3600.0  # 1 hour

    def __init__(
        self,
        allow_absolute_paths: bool = False,
        allowed_commands: list[str] | None = None,
        blocked_commands: list[str] | None = None,
    ):
        """
        Initialize validator with configuration.

        Args:
            allow_absolute_paths: Whether to allow absolute paths in commands
            allowed_commands: Whitelist of allowed command executables (if set, only these are allowed)
            blocked_commands: Blacklist of blocked command executables
        """
        self.allow_absolute_paths = allow_absolute_paths
        self.allowed_commands = set(allowed_commands) if allowed_commands else None
        self.blocked_commands = set(
            blocked_commands
            or [
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
                "netcat",  # Network tools (potential exfiltration)
                "bash",
                "sh",
                "zsh",
                "fish",  # Shells (unless explicitly allowed)
                "eval",
                "exec",  # Dangerous builtins
            ]
        )

    def validate_provider_id(self, provider_id: Any) -> ValidationResult:
        """
        Validate a provider ID.

        Rules:
        - Must be a non-empty string
        - Must start with a letter
        - Only alphanumeric, hyphens, underscores allowed
        - Max 64 characters
        """
        result = ValidationResult(valid=True)

        if provider_id is None:
            result.add_error("provider_id", "Provider ID is required")
            return result

        if not isinstance(provider_id, str):
            result.add_error("provider_id", "Provider ID must be a string", provider_id)
            return result

        if not provider_id:
            result.add_error("provider_id", "Provider ID cannot be empty")
            return result

        if len(provider_id) > self.MAX_PROVIDER_ID_LENGTH:
            result.add_error(
                "provider_id",
                f"Provider ID exceeds maximum length ({len(provider_id)} > {self.MAX_PROVIDER_ID_LENGTH})",
                provider_id,
            )
            return result

        if not PROVIDER_ID_PATTERN.match(provider_id):
            result.add_error(
                "provider_id",
                "Provider ID must start with letter, contain only alphanums, hyphens, underscores",
                provider_id,
            )

        # Check for potential injection
        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(provider_id):
                result.add_error(
                    "provider_id",
                    "Provider ID contains potentially dangerous characters",
                    provider_id,
                )
                break

        return result

    def validate_tool_name(self, tool_name: Any) -> ValidationResult:
        """
        Validate a tool name.

        Rules:
        - Must be a non-empty string
        - Must start with a letter
        - Only alphanumeric, underscores, dots, slashes allowed
        - Max 128 characters
        """
        result = ValidationResult(valid=True)

        if tool_name is None:
            result.add_error("tool_name", "Tool name is required")
            return result

        if not isinstance(tool_name, str):
            result.add_error("tool_name", "Tool name must be a string", tool_name)
            return result

        if not tool_name:
            result.add_error("tool_name", "Tool name cannot be empty")
            return result

        if len(tool_name) > self.MAX_TOOL_NAME_LENGTH:
            result.add_error(
                "tool_name",
                f"Tool name exceeds maximum length ({len(tool_name)} > {self.MAX_TOOL_NAME_LENGTH})",
                tool_name,
            )
            return result

        if not TOOL_NAME_PATTERN.match(tool_name):
            result.add_error(
                "tool_name",
                "Tool name must start with letter, contain only alphanums, underscores, dots, slashes",
                tool_name,
            )

        # Check for path traversal
        if ".." in tool_name:
            result.add_error(
                "tool_name",
                "Tool name cannot contain path traversal sequences",
                tool_name,
            )

        return result

    def validate_arguments(
        self,
        arguments: Any,
        max_size: int | None = None,
        max_depth: int | None = None,
    ) -> ValidationResult:
        """
        Validate tool arguments.

        Rules:
        - Must be a dictionary
        - Keys must be strings
        - Total size must be within limit
        - Nesting depth must be within limit
        """
        result = ValidationResult(valid=True)
        max_size = max_size or self.MAX_ARGUMENT_SIZE_BYTES
        max_depth = max_depth or self.MAX_ARGUMENT_DEPTH

        if arguments is None:
            # None is acceptable, will be treated as empty dict
            return result

        if not isinstance(arguments, dict):
            result.add_error("arguments", "Arguments must be a dictionary", type(arguments).__name__)
            return result

        # Validate size
        try:
            import json

            serialized = json.dumps(arguments)
            size = len(serialized.encode("utf-8"))
            if size > max_size:
                result.add_error(
                    "arguments",
                    f"Arguments exceed maximum size ({size} > {max_size} bytes)",
                )
                return result
        except (TypeError, ValueError) as e:
            result.add_error("arguments", f"Arguments must be JSON-serializable: {e}")
            return result

        # Validate structure recursively
        self._validate_argument_structure(arguments, result, "arguments", 0, max_depth)

        return result

    def _validate_argument_structure(
        self, obj: Any, result: ValidationResult, path: str, depth: int, max_depth: int
    ) -> None:
        """Recursively validate argument structure."""
        if depth > max_depth:
            result.add_error(path, f"Arguments exceed maximum nesting depth ({max_depth})")
            return

        if isinstance(obj, dict):
            for key, value in obj.items():
                if not isinstance(key, str):
                    result.add_error(
                        f"{path}.{key}",
                        "Argument keys must be strings",
                        type(key).__name__,
                    )
                    continue

                # Check for empty keys
                if not key:
                    result.add_error(path, "Argument keys cannot be empty")
                    continue

                self._validate_argument_structure(value, result, f"{path}.{key}", depth + 1, max_depth)

        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                self._validate_argument_structure(item, result, f"{path}[{i}]", depth + 1, max_depth)

        elif isinstance(obj, str):
            # Check for very long strings that might be DoS attempts
            if len(obj) > 1_000_000:  # 1MB string
                result.add_error(path, f"String value exceeds maximum length ({len(obj)} > 1000000)")

    def validate_timeout(self, timeout: Any) -> ValidationResult:
        """
        Validate a timeout value.

        Rules:
        - Must be a number (int or float)
        - Must be positive
        - Must be within reasonable bounds
        """
        result = ValidationResult(valid=True)

        if timeout is None:
            # Default will be used
            return result

        if not isinstance(timeout, int | float):
            result.add_error("timeout", "Timeout must be a number", type(timeout).__name__)
            return result

        if timeout < self.MIN_TIMEOUT:
            result.add_error(
                "timeout",
                f"Timeout must be at least {self.MIN_TIMEOUT} seconds",
                timeout,
            )

        if timeout > self.MAX_TIMEOUT:
            result.add_error("timeout", f"Timeout cannot exceed {self.MAX_TIMEOUT} seconds", timeout)

        return result

    def validate_command(self, command: Any) -> ValidationResult:
        """
        Validate a command for subprocess execution.

        Rules:
        - Must be a non-empty list of strings
        - First element is the executable
        - No dangerous patterns in arguments
        - Executable must not be in blocklist
        """
        result = ValidationResult(valid=True)

        if command is None:
            result.add_error("command", "Command is required")
            return result

        if not isinstance(command, list):
            result.add_error("command", "Command must be a list of strings", type(command).__name__)
            return result

        if not command:
            result.add_error("command", "Command list cannot be empty")
            return result

        if len(command) > self.MAX_COMMAND_ARGS:
            result.add_error(
                "command",
                f"Command has too many arguments ({len(command)} > {self.MAX_COMMAND_ARGS})",
            )
            return result

        # Validate each element
        for i, arg in enumerate(command):
            if not isinstance(arg, str):
                result.add_error(
                    f"command[{i}]",
                    "Command arguments must be strings",
                    type(arg).__name__,
                )
                continue

            # Check for injection patterns
            for pattern in DANGEROUS_PATTERNS:
                if pattern.search(arg):
                    result.add_error(
                        f"command[{i}]",
                        "Command argument contains potentially dangerous characters",
                        arg,
                    )
                    break

        # Validate executable (first element)
        if command and isinstance(command[0], str):
            executable = command[0]

            # Extract base name for checking
            import os

            base_name = os.path.basename(executable)

            # Check against blocklist
            if base_name in self.blocked_commands:
                result.add_error("command[0]", f"Executable '{base_name}' is not allowed", executable)

            # Check against allowlist if set
            if self.allowed_commands is not None:
                if base_name not in self.allowed_commands:
                    result.add_error(
                        "command[0]",
                        f"Executable '{base_name}' is not in the allowed list",
                        executable,
                    )

            # Check for absolute paths if not allowed
            if not self.allow_absolute_paths and executable.startswith("/"):
                result.add_warning("command[0]", "Using absolute paths is discouraged", executable)

        return result

    def validate_docker_image(self, image: Any) -> ValidationResult:
        """
        Validate a Docker image name.

        Rules:
        - Must be a non-empty string
        - Must match Docker image naming conventions
        - No dangerous patterns
        """
        result = ValidationResult(valid=True)

        if image is None:
            result.add_error("image", "Docker image is required")
            return result

        if not isinstance(image, str):
            result.add_error("image", "Docker image must be a string", type(image).__name__)
            return result

        if not image:
            result.add_error("image", "Docker image cannot be empty")
            return result

        if len(image) > 255:
            result.add_error(
                "image",
                f"Docker image name exceeds maximum length ({len(image)} > 255)",
                image,
            )
            return result

        # Check for injection patterns
        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(image):
                result.add_error(
                    "image",
                    "Docker image contains potentially dangerous characters",
                    image,
                )
                return result

        # Validate format (relaxed pattern for flexibility)
        if not DOCKER_IMAGE_PATTERN.match(image):
            # Try a more lenient check
            if not re.match(r"^[\w.\-/:@]+$", image):
                result.add_error("image", "Docker image has invalid format", image)

        return result

    def validate_environment_variables(self, env: Any) -> ValidationResult:
        """
        Validate environment variables.

        Rules:
        - Must be a dictionary (or None)
        - Keys must be valid env var names
        - Values must be strings
        - Limited number of variables
        """
        result = ValidationResult(valid=True)

        if env is None:
            return result

        if not isinstance(env, dict):
            result.add_error("env", "Environment variables must be a dictionary", type(env).__name__)
            return result

        if len(env) > self.MAX_ENV_VARS:
            result.add_error(
                "env",
                f"Too many environment variables ({len(env)} > {self.MAX_ENV_VARS})",
            )
            return result

        for key, value in env.items():
            # Validate key
            if not isinstance(key, str):
                result.add_error(
                    f"env[{key}]",
                    "Environment variable key must be a string",
                    type(key).__name__,
                )
                continue

            if not key:
                result.add_error("env", "Environment variable key cannot be empty")
                continue

            if len(key) > self.MAX_ENV_KEY_LENGTH:
                result.add_error(
                    f"env[{key}]",
                    f"Environment variable key exceeds maximum length ({len(key)} > {self.MAX_ENV_KEY_LENGTH})",
                )
                continue

            if not ENV_KEY_PATTERN.match(key):
                result.add_error(
                    f"env[{key}]",
                    "Environment variable key has invalid format (must match [A-Za-z_][A-Za-z0-9_]*)",
                    key,
                )

            # Validate value
            if not isinstance(value, str):
                result.add_error(
                    f"env[{key}]",
                    "Environment variable value must be a string",
                    type(value).__name__,
                )
                continue

            if len(value) > self.MAX_ENV_VALUE_LENGTH:
                result.add_error(
                    f"env[{key}]",
                    f"Environment variable value exceeds maximum length ({len(value)} > {self.MAX_ENV_VALUE_LENGTH})",
                )

            # Check for dangerous patterns in values
            for pattern in DANGEROUS_PATTERNS[:3]:  # Only check most dangerous
                if pattern.search(value):
                    result.add_warning(
                        f"env[{key}]",
                        "Environment variable value contains potentially dangerous characters",
                    )
                    break

        return result

    def validate_all(
        self,
        provider_id: str | None = None,
        tool_name: str | None = None,
        arguments: dict[str, Any] | None = None,
        timeout: float | None = None,
        command: list[str] | None = None,
        image: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ValidationResult:
        """
        Validate multiple inputs at once.

        Only validates non-None inputs.
        """
        result = ValidationResult(valid=True)

        if provider_id is not None:
            result.merge(self.validate_provider_id(provider_id))

        if tool_name is not None:
            result.merge(self.validate_tool_name(tool_name))

        if arguments is not None:
            result.merge(self.validate_arguments(arguments))

        if timeout is not None:
            result.merge(self.validate_timeout(timeout))

        if command is not None:
            result.merge(self.validate_command(command))

        if image is not None:
            result.merge(self.validate_docker_image(image))

        if env is not None:
            result.merge(self.validate_environment_variables(env))

        return result


# --- Convenience Functions ---

# Global validator instance with default settings
_default_validator = InputValidator()


def validate_provider_id(provider_id: Any) -> ValidationResult:
    """Validate a provider ID using default validator."""
    return _default_validator.validate_provider_id(provider_id)


def validate_tool_name(tool_name: Any) -> ValidationResult:
    """Validate a tool name using default validator."""
    return _default_validator.validate_tool_name(tool_name)


def validate_arguments(arguments: Any, max_size: int | None = None, max_depth: int | None = None) -> ValidationResult:
    """Validate tool arguments using default validator."""
    return _default_validator.validate_arguments(arguments, max_size, max_depth)


def validate_timeout(timeout: Any) -> ValidationResult:
    """Validate a timeout value using default validator."""
    return _default_validator.validate_timeout(timeout)


def validate_command(command: Any) -> ValidationResult:
    """Validate a command using default validator."""
    return _default_validator.validate_command(command)


def validate_docker_image(image: Any) -> ValidationResult:
    """Validate a Docker image using default validator."""
    return _default_validator.validate_docker_image(image)


def validate_environment_variables(env: Any) -> ValidationResult:
    """Validate environment variables using default validator."""
    return _default_validator.validate_environment_variables(env)
