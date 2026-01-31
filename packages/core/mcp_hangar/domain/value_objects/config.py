"""Configuration value objects for providers.

Contains:
- CommandLine - subprocess command configuration
- DockerImage - docker image specification
- Endpoint - remote endpoint URL
- EnvironmentVariables - environment configuration
- IdleTTL - idle time-to-live
- HealthCheckInterval - health check timing (also in health.py)
- MaxConsecutiveFailures - failure threshold
- TimeoutSeconds - timeout configuration
- HTTP transport configuration classes
"""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class CommandLine:
    """Command line with arguments for subprocess providers.

    Rules:
    - Non-empty command list
    - First element is the command/executable
    - Remaining elements are arguments
    """

    command: str
    arguments: tuple

    def __init__(self, command: str, *arguments: str):
        if not command:
            raise ValueError("Command cannot be empty")
        # Use object.__setattr__ because dataclass is frozen
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "arguments", tuple(arguments))

    @classmethod
    def from_list(cls, command_list: list[str]) -> "CommandLine":
        """Create from a list of strings."""
        if not command_list:
            raise ValueError("Command list cannot be empty")
        return cls(command_list[0], *command_list[1:])

    def to_list(self) -> list[str]:
        """Convert to list format."""
        return [self.command, *self.arguments]

    def __str__(self) -> str:
        return " ".join(self.to_list())


@dataclass(frozen=True)
class DockerImage:
    """Docker image specification.

    Rules:
    - Non-empty string
    - Valid docker image format (name:tag or registry/name:tag)
    """

    value: str

    def __init__(self, value: str):
        if not value:
            raise ValueError("DockerImage cannot be empty")
        # Basic validation - could be more sophisticated
        if not re.match(r"^[\w.\-/:]+$", value):
            raise ValueError("Invalid docker image format")
        object.__setattr__(self, "value", value)

    @property
    def name(self) -> str:
        """Extract image name without tag."""
        return self.value.split(":")[0]

    @property
    def tag(self) -> str:
        """Extract tag, defaults to 'latest'."""
        parts = self.value.split(":")
        return parts[1] if len(parts) > 1 else "latest"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Endpoint:
    """Remote endpoint URL.

    Rules:
    - Non-empty string
    - Valid URL format
    - Supported schemes: http, https, ws, wss
    """

    value: str

    def __init__(self, value: str):
        if not value:
            raise ValueError("Endpoint cannot be empty")

        parsed = urlparse(value)

        # Check for valid scheme - urlparse treats "localhost:8080" as scheme="localhost"
        # If netloc is empty and path exists, it's likely missing scheme (e.g., "localhost:8080")
        if not parsed.netloc and parsed.path:
            raise ValueError("Endpoint must include scheme (http, https, ws, wss)")

        # Check that we have a host
        if not parsed.netloc:
            raise ValueError("Endpoint must include host")

        # Validate scheme
        if parsed.scheme not in ["http", "https", "ws", "wss"]:
            raise ValueError(f"Unsupported endpoint scheme: {parsed.scheme}")

        object.__setattr__(self, "value", value)

    @property
    def scheme(self) -> str:
        return urlparse(self.value).scheme

    @property
    def host(self) -> str:
        return urlparse(self.value).netloc

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class EnvironmentVariables:
    """Environment variables for provider execution.

    Rules:
    - Keys are non-empty strings
    - Values are strings
    - Immutable after creation
    """

    variables: dict[str, str]

    def __init__(self, variables: dict[str, str] | None = None):
        vars_dict = variables or {}

        # Validate keys
        for key in vars_dict.keys():
            if not key:
                raise ValueError("Environment variable key cannot be empty")
            if not isinstance(key, str):
                raise ValueError("Environment variable key must be a string")

        # Create immutable copy
        object.__setattr__(self, "variables", dict(vars_dict))

    def get(self, key: str, default: str | None = None) -> str | None:
        """Get environment variable value."""
        return self.variables.get(key, default)

    def __getitem__(self, key: str) -> str:
        return self.variables[key]

    def __contains__(self, key: str) -> bool:
        return key in self.variables

    def __len__(self) -> int:
        return len(self.variables)

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary (returns a copy)."""
        return dict(self.variables)


@dataclass(frozen=True)
class IdleTTL:
    """Time-to-live for idle providers in seconds.

    Rules:
    - Positive integer
    - Reasonable range: 1 to 86400 seconds (1 day)
    """

    seconds: int

    def __init__(self, seconds: int):
        if seconds <= 0:
            raise ValueError("IdleTTL must be positive")
        if seconds > 86400:
            raise ValueError("IdleTTL cannot exceed 86400 seconds (1 day)")
        object.__setattr__(self, "seconds", seconds)

    def __int__(self) -> int:
        return self.seconds

    def __str__(self) -> str:
        return f"{self.seconds}s"


@dataclass(frozen=True)
class HealthCheckInterval:
    """Interval between health checks in seconds.

    Rules:
    - Positive integer
    - Reasonable range: 5 to 3600 seconds (1 hour)
    """

    seconds: int

    def __init__(self, seconds: int):
        if seconds <= 0:
            raise ValueError("HealthCheckInterval must be positive")
        if seconds < 5:
            raise ValueError("HealthCheckInterval must be at least 5 seconds")
        if seconds > 3600:
            raise ValueError("HealthCheckInterval cannot exceed 3600 seconds (1 hour)")
        object.__setattr__(self, "seconds", seconds)

    def __int__(self) -> int:
        return self.seconds

    def __str__(self) -> str:
        return f"{self.seconds}s"


@dataclass(frozen=True)
class MaxConsecutiveFailures:
    """Maximum consecutive failures before degradation.

    Rules:
    - Positive integer
    - Reasonable range: 1 to 100
    """

    count: int

    def __init__(self, count: int):
        if count <= 0:
            raise ValueError("MaxConsecutiveFailures must be positive")
        if count > 100:
            raise ValueError("MaxConsecutiveFailures cannot exceed 100")
        object.__setattr__(self, "count", count)

    def __int__(self) -> int:
        return self.count

    def __str__(self) -> str:
        return str(self.count)


@dataclass(frozen=True)
class TimeoutSeconds:
    """Timeout duration in seconds.

    Rules:
    - Positive number (int or float)
    - Reasonable range: 0.1 to 3600 seconds
    """

    seconds: float

    def __init__(self, seconds: float):
        if seconds <= 0:
            raise ValueError("TimeoutSeconds must be positive")
        if seconds > 3600:
            raise ValueError("TimeoutSeconds cannot exceed 3600 seconds (1 hour)")
        object.__setattr__(self, "seconds", float(seconds))

    def __float__(self) -> float:
        return self.seconds

    def __str__(self) -> str:
        return f"{self.seconds}s"


# --- HTTP Transport Value Objects ---


class HttpAuthType(Enum):
    """Authentication type for HTTP providers.

    Attributes:
        NONE: No authentication required.
        API_KEY: API key sent in a header.
        BEARER: Bearer token authentication.
        BASIC: HTTP Basic authentication.
    """

    NONE = "none"
    API_KEY = "api_key"
    BEARER = "bearer"
    BASIC = "basic"

    def __str__(self) -> str:
        return self.value

    @classmethod
    def normalize(cls, value: "str | HttpAuthType | None") -> "HttpAuthType":
        """Normalize auth type value to HttpAuthType enum."""
        if value is None:
            return cls.NONE
        if isinstance(value, cls):
            return value
        return cls(value)


@dataclass(frozen=True)
class HttpAuthConfig:
    """Authentication configuration for HTTP providers.

    Immutable value object containing auth credentials.
    Secrets should be passed via environment variable interpolation.

    Attributes:
        auth_type: Type of authentication to use.
        api_key: API key for api_key auth (header value).
        api_key_header: Header name for API key (default: X-API-Key).
        bearer_token: Bearer token for bearer auth.
        basic_username: Username for basic auth.
        basic_password: Password for basic auth.
    """

    auth_type: HttpAuthType = HttpAuthType.NONE
    api_key: str | None = None
    api_key_header: str = "X-API-Key"
    bearer_token: str | None = None
    basic_username: str | None = None
    basic_password: str | None = None

    def __post_init__(self) -> None:
        """Validate auth configuration."""
        if self.auth_type == HttpAuthType.API_KEY and not self.api_key:
            raise ValueError("api_key is required for api_key auth type")
        if self.auth_type == HttpAuthType.BEARER and not self.bearer_token:
            raise ValueError("bearer_token is required for bearer auth type")
        if self.auth_type == HttpAuthType.BASIC:
            if not self.basic_username or not self.basic_password:
                raise ValueError("basic_username and basic_password are required for basic auth type")

    def get_headers(self) -> dict[str, str]:
        """Get authentication headers for HTTP requests.

        Returns:
            Dictionary of headers to add to requests.
        """
        if self.auth_type == HttpAuthType.NONE:
            return {}

        if self.auth_type == HttpAuthType.API_KEY:
            return {self.api_key_header: self.api_key}

        if self.auth_type == HttpAuthType.BEARER:
            return {"Authorization": f"Bearer {self.bearer_token}"}

        if self.auth_type == HttpAuthType.BASIC:
            import base64

            credentials = f"{self.basic_username}:{self.basic_password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            return {"Authorization": f"Basic {encoded}"}

        return {}

    @classmethod
    def from_dict(cls, config: dict[str, Any] | None) -> "HttpAuthConfig":
        """Create from dictionary configuration.

        Args:
            config: Dictionary with auth configuration or None for no auth.

        Returns:
            HttpAuthConfig instance.
        """
        if not config:
            return cls()

        auth_type = HttpAuthType.normalize(config.get("type"))

        return cls(
            auth_type=auth_type,
            api_key=config.get("api_key"),
            api_key_header=config.get("api_key_header", "X-API-Key"),
            bearer_token=config.get("bearer_token"),
            basic_username=config.get("username"),
            basic_password=config.get("password"),
        )


@dataclass(frozen=True)
class HttpTlsConfig:
    """TLS configuration for HTTPS connections.

    Attributes:
        verify_ssl: Whether to verify SSL certificates.
        ca_cert_path: Path to custom CA certificate file.
    """

    verify_ssl: bool = True
    ca_cert_path: str | None = None

    @classmethod
    def from_dict(cls, config: dict[str, Any] | None) -> "HttpTlsConfig":
        """Create from dictionary configuration."""
        if not config:
            return cls()

        return cls(
            verify_ssl=config.get("verify_ssl", True),
            ca_cert_path=config.get("ca_cert_path"),
        )


@dataclass(frozen=True)
class HttpTransportConfig:
    """Complete HTTP transport configuration for remote providers.

    Attributes:
        connect_timeout: Connection timeout in seconds.
        read_timeout: Read timeout in seconds.
        max_retries: Maximum number of retry attempts.
        retry_backoff_factor: Exponential backoff factor for retries.
        keep_alive: Whether to use HTTP keep-alive.
        extra_headers: Additional headers to include in all requests.
    """

    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    max_retries: int = 3
    retry_backoff_factor: float = 0.5
    keep_alive: bool = True
    extra_headers: dict[str, str] | None = None

    def __post_init__(self) -> None:
        """Validate transport configuration."""
        if self.connect_timeout <= 0:
            raise ValueError("connect_timeout must be positive")
        if self.read_timeout <= 0:
            raise ValueError("read_timeout must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.retry_backoff_factor < 0:
            raise ValueError("retry_backoff_factor cannot be negative")

    @classmethod
    def from_dict(cls, config: dict[str, Any] | None) -> "HttpTransportConfig":
        """Create from dictionary configuration."""
        if not config:
            return cls()

        return cls(
            connect_timeout=config.get("connect_timeout", 10.0),
            read_timeout=config.get("read_timeout", 30.0),
            max_retries=config.get("max_retries", 3),
            retry_backoff_factor=config.get("retry_backoff_factor", 0.5),
            keep_alive=config.get("keep_alive", True),
            extra_headers=config.get("headers"),
        )
