"""
Value Objects for the MCP Registry domain.

Value objects are immutable, validated domain primitives that encapsulate
business rules and prevent invalid states. They replace primitive obsession
with strongly-typed domain concepts.
"""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any
from urllib.parse import urlparse
import uuid

# --- Authentication & Authorization Value Objects ---


class PrincipalType(Enum):
    """Type of authenticated principal.

    Attributes:
        USER: Human user authenticated via JWT/OIDC or session.
        SERVICE_ACCOUNT: Non-human identity, typically authenticated via API key.
        SYSTEM: Internal system principal for background operations.
    """

    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    SYSTEM = "system"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class PrincipalId:
    """Unique identifier for an authenticated principal.

    PrincipalIds follow the format: [type:]identifier
    Examples: "user:john@example.com", "service:ci-pipeline", "system"

    Attributes:
        value: The identifier string (1-256 chars, alphanumeric + -_:@.)
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("PrincipalId cannot be empty")
        if len(self.value) > 256:
            raise ValueError("PrincipalId must be 1-256 characters")
        # Allow alphanumeric and -_:@.
        allowed_chars = set("-_:@.")
        if not all(c.isalnum() or c in allowed_chars for c in self.value):
            raise ValueError(
                f"PrincipalId contains invalid characters: {self.value!r}. Only alphanumeric and -_:@. are allowed."
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Principal:
    """Authenticated identity making a request.

    Immutable value object representing a verified identity with associated
    groups and metadata. Used throughout the authorization layer.

    Attributes:
        id: Unique identifier for the principal.
        type: Classification of the principal (user, service_account, system).
        tenant_id: Optional tenant identifier for multi-tenancy.
        groups: Immutable set of group memberships.
        metadata: Additional identity information (email, display_name, etc.).
    """

    id: PrincipalId
    type: PrincipalType
    tenant_id: str | None = None
    groups: frozenset[str] = frozenset()
    metadata: dict | None = None

    def __post_init__(self) -> None:
        # Ensure metadata is a new dict copy to maintain immutability semantics
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})
        else:
            object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def system(cls) -> "Principal":
        """Return the system principal for internal operations.

        The system principal has implicit full access and is used for
        background tasks, health checks, and internal operations.
        """
        return cls(
            id=PrincipalId("system"),
            type=PrincipalType.SYSTEM,
        )

    @classmethod
    def anonymous(cls) -> "Principal":
        """Return anonymous principal for unauthenticated requests.

        Used when authentication is optional and no credentials provided.
        """
        return cls(
            id=PrincipalId("anonymous"),
            type=PrincipalType.USER,
        )

    def is_system(self) -> bool:
        """Check if this is the system principal."""
        return self.type == PrincipalType.SYSTEM

    def is_anonymous(self) -> bool:
        """Check if this is the anonymous principal."""
        return self.id.value == "anonymous"

    def in_group(self, group: str) -> bool:
        """Check if principal is a member of the specified group."""
        return group in self.groups


@dataclass(frozen=True)
class Permission:
    """A specific permission that can be granted.

    Permissions follow the format: resource_type:action:resource_id
    Examples: "provider:read:*", "tool:invoke:math:add", "config:update:*"

    Wildcard (*) matches any value for that component.

    Attributes:
        resource_type: Type of resource (provider, tool, config, audit, metrics).
        action: Operation to perform (create, read, update, delete, invoke, list, start, stop).
        resource_id: Specific resource or wildcard (*) for any.
    """

    resource_type: str
    action: str
    resource_id: str = "*"

    def __post_init__(self) -> None:
        if not self.resource_type:
            raise ValueError("Permission resource_type cannot be empty")
        if not self.action:
            raise ValueError("Permission action cannot be empty")
        if not self.resource_id:
            raise ValueError("Permission resource_id cannot be empty")

    def matches(self, resource_type: str, action: str, resource_id: str) -> bool:
        """Check if this permission grants access to the requested operation.

        Args:
            resource_type: The type of resource being accessed.
            action: The action being performed.
            resource_id: The specific resource identifier.

        Returns:
            True if this permission grants access, False otherwise.
        """
        if self.resource_type != "*" and self.resource_type != resource_type:
            return False
        if self.action != "*" and self.action != action:
            return False
        if self.resource_id != "*" and self.resource_id != resource_id:
            return False
        return True

    def __str__(self) -> str:
        return f"{self.resource_type}:{self.action}:{self.resource_id}"

    @classmethod
    def parse(cls, permission_str: str) -> "Permission":
        """Parse permission from string format 'resource:action[:id]'.

        Args:
            permission_str: Permission string to parse.

        Returns:
            Parsed Permission object.

        Raises:
            ValueError: If the format is invalid.

        Examples:
            >>> Permission.parse("provider:read")
            Permission(resource_type='provider', action='read', resource_id='*')
            >>> Permission.parse("tool:invoke:math:add")
            Permission(resource_type='tool', action='invoke', resource_id='math:add')
        """
        parts = permission_str.split(":", 2)  # Split into at most 3 parts
        if len(parts) < 2:
            raise ValueError(
                f"Invalid permission format: {permission_str!r}. Expected 'resource:action' or 'resource:action:id'."
            )
        if len(parts) == 2:
            return cls(resource_type=parts[0], action=parts[1])
        return cls(resource_type=parts[0], action=parts[1], resource_id=parts[2])


@dataclass(frozen=True)
class Role:
    """Named collection of permissions.

    Roles group related permissions for easier assignment and management.
    Built-in roles include: admin, provider-admin, developer, viewer, auditor.

    Attributes:
        name: Unique role identifier.
        permissions: Immutable set of permissions granted by this role.
        description: Human-readable description of the role's purpose.
    """

    name: str
    permissions: frozenset[Permission]
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Role name cannot be empty")
        if not self.name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                f"Role name contains invalid characters: {self.name!r}. Only alphanumeric and -_ are allowed."
            )

    def has_permission(self, resource_type: str, action: str, resource_id: str = "*") -> bool:
        """Check if this role grants the requested permission.

        Args:
            resource_type: Type of resource being accessed.
            action: Operation being performed.
            resource_id: Specific resource or '*' for any.

        Returns:
            True if any permission in this role matches the request.
        """
        return any(p.matches(resource_type, action, resource_id) for p in self.permissions)

    def __str__(self) -> str:
        return self.name


# --- Enums ---


class ProviderState(Enum):
    """Provider lifecycle states.

    Represents the finite state machine for provider lifecycle management.

    State machine transitions:
        - COLD -> INITIALIZING (on start)
        - INITIALIZING -> READY (on success) | DEAD (on failure) | DEGRADED (on max failures)
        - READY -> COLD (on shutdown) | DEAD (on client death) | DEGRADED (on health failures)
        - DEGRADED -> INITIALIZING (on retry) | COLD (on shutdown)
        - DEAD -> INITIALIZING (on retry) | DEGRADED (on max failures)

    Attributes:
        COLD: Provider is not running, no resources allocated.
        INITIALIZING: Provider is starting up, handshake in progress.
        READY: Provider is running and accepting requests.
        DEGRADED: Provider has failures but may recover after backoff.
        DEAD: Provider has failed fatally and requires manual intervention or retry.
    """

    COLD = "cold"
    INITIALIZING = "initializing"
    READY = "ready"
    DEGRADED = "degraded"
    DEAD = "dead"

    def __str__(self) -> str:
        """Return the string representation of the state."""
        return self.value

    @property
    def can_accept_requests(self) -> bool:
        """Check if provider can accept tool invocation requests.

        Returns:
            True if provider is in READY state, False otherwise.
        """
        return self == ProviderState.READY

    @property
    def can_start(self) -> bool:
        """Check if provider can be started from this state.

        Returns:
            True if provider can transition to INITIALIZING, False otherwise.
        """
        return self in (ProviderState.COLD, ProviderState.DEAD, ProviderState.DEGRADED)


class HealthStatus(Enum):
    """Health status for providers.

    Represents the externally visible health classification of a provider.

    Attributes:
        HEALTHY: Provider is fully operational with no recent failures.
        DEGRADED: Provider is operational but has experienced recent failures.
        UNHEALTHY: Provider is not operational or has exceeded failure threshold.
        UNKNOWN: Provider health cannot be determined (e.g., not started).
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        """Return the string representation of the status."""
        return self.value

    @classmethod
    def from_state(cls, state: ProviderState, consecutive_failures: int = 0) -> "HealthStatus":
        """Derive health status from provider state and failure count.

        Args:
            state: The current provider state.
            consecutive_failures: Number of consecutive failures (default: 0).

        Returns:
            The derived HealthStatus based on state and failures.

        Example:
            >>> HealthStatus.from_state(ProviderState.READY, 0)
            <HealthStatus.HEALTHY: 'healthy'>
        """
        if state == ProviderState.READY:
            if consecutive_failures == 0:
                return cls.HEALTHY
            return cls.DEGRADED
        elif state == ProviderState.DEGRADED:
            return cls.UNHEALTHY
        elif state == ProviderState.COLD:
            return cls.UNKNOWN
        else:
            return cls.UNHEALTHY


class ProviderMode(Enum):
    """Mode for running a provider."""

    SUBPROCESS = "subprocess"
    DOCKER = "docker"
    CONTAINER = "container"  # Alias for docker mode
    REMOTE = "remote"
    GROUP = "group"  # Provider group with load balancing

    def __str__(self) -> str:
        return self.value

    @classmethod
    def normalize(cls, value: "str | ProviderMode") -> "ProviderMode":
        """Normalize mode value to ProviderMode enum."""
        if isinstance(value, cls):
            return value
        # Handle string values - return corresponding enum
        return cls(value)


class LoadBalancerStrategy(Enum):
    """Load balancing strategy for provider groups."""

    ROUND_ROBIN = "round_robin"
    WEIGHTED_ROUND_ROBIN = "weighted_round_robin"
    LEAST_CONNECTIONS = "least_connections"
    RANDOM = "random"
    PRIORITY = "priority"  # Always prefer lowest priority member

    def __str__(self) -> str:
        return self.value


class GroupState(Enum):
    """Provider group lifecycle states."""

    INACTIVE = "inactive"  # No members started
    PARTIAL = "partial"  # Some members healthy, below min_healthy
    HEALTHY = "healthy"  # >= min_healthy members ready
    DEGRADED = "degraded"  # Circuit breaker tripped

    def __str__(self) -> str:
        return self.value

    @property
    def can_accept_requests(self) -> bool:
        """Check if group can accept tool invocation requests."""
        return self in (GroupState.HEALTHY, GroupState.PARTIAL)


# --- Identity Value Objects ---


class ProviderId:
    """Unique identifier for a provider.

    Validates and encapsulates provider identity with strict rules:
    - Non-empty string
    - Alphanumeric, hyphens, underscores only
    - Max 64 characters

    Attributes:
        value: The validated provider identifier string.

    Raises:
        ValueError: If the provided value violates validation rules.

    Example:
        >>> provider_id = ProviderId("my-provider-1")
        >>> str(provider_id)
        'my-provider-1'
    """

    _VALID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
    _MAX_LENGTH = 64

    def __init__(self, value: str):
        """Initialize ProviderId with validation.

        Args:
            value: The provider identifier string to validate.

        Raises:
            ValueError: If value is empty, too long, or contains invalid characters.
        """
        if not value:
            raise ValueError("ProviderId cannot be empty")
        if len(value) > self._MAX_LENGTH:
            raise ValueError(f"ProviderId cannot exceed {self._MAX_LENGTH} characters")
        if not self._VALID_PATTERN.match(value):
            raise ValueError("ProviderId must contain only alphanumeric characters, hyphens, and underscores")
        self._value = value

    @property
    def value(self) -> str:
        """Get the raw identifier string."""
        return self._value

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"ProviderId('{self._value}')"

    def __eq__(self, other) -> bool:
        if isinstance(other, str):
            return self._value == other
        if not isinstance(other, ProviderId):
            return False
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)


class ToolName:
    """Name of a tool provided by a provider.

    Validates tool names with the following rules:
    - Non-empty string
    - Alphanumeric, hyphens, underscores, dots allowed (for namespaced tools)
    - Max 128 characters

    Attributes:
        value: The validated tool name string.

    Raises:
        ValueError: If the provided value violates validation rules.

    Example:
        >>> tool = ToolName("math.add")
        >>> str(tool)
        'math.add'
    """

    _VALID_PATTERN = re.compile(r"^[a-zA-Z0-9_.\-]+$")
    _MAX_LENGTH = 128

    def __init__(self, value: str):
        """Initialize ToolName with validation.

        Args:
            value: The tool name string to validate.

        Raises:
            ValueError: If value is empty, too long, or contains invalid characters.
        """
        if not value:
            raise ValueError("ToolName cannot be empty")
        if len(value) > self._MAX_LENGTH:
            raise ValueError(f"ToolName cannot exceed {self._MAX_LENGTH} characters")
        if not self._VALID_PATTERN.match(value):
            raise ValueError("ToolName must contain only alphanumeric characters, hyphens, underscores, and dots")
        self._value = value

    @property
    def value(self) -> str:
        """Get the raw tool name string."""
        return self._value

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"ToolName('{self._value}')"

    def __eq__(self, other) -> bool:
        if isinstance(other, str):
            return self._value == other
        if not isinstance(other, ToolName):
            return False
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)


@dataclass(frozen=True)
class CorrelationId:
    """Correlation ID for tracing requests.

    Rules:
    - Non-empty string
    - Valid UUID v4 format (or auto-generated)
    """

    value: str

    def __init__(self, value: str | None = None):
        if value is None:
            # Generate new UUID
            value = str(uuid.uuid4())
        else:
            # Validate existing UUID
            if not value:
                raise ValueError("CorrelationId cannot be empty")
            try:
                uuid.UUID(value, version=4)
            except ValueError:
                raise ValueError("CorrelationId must be a valid UUID v4")

        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"CorrelationId('{self.value}')"


# --- Configuration Value Objects ---


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


# --- Timing Value Objects ---


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


# --- Provider Configuration ---


@dataclass(frozen=True)
class ProviderConfig:
    """Complete configuration for a provider.

    Encapsulates all configuration options in a validated, immutable object.
    """

    provider_id: ProviderId
    mode: ProviderMode
    command: CommandLine | None = None
    image: DockerImage | None = None
    endpoint: Endpoint | None = None
    env: EnvironmentVariables | None = None
    idle_ttl: IdleTTL = None
    health_check_interval: HealthCheckInterval = None
    max_consecutive_failures: MaxConsecutiveFailures = None

    def __init__(
        self,
        provider_id: str,
        mode: str,
        command: list[str] | None = None,
        image: str | None = None,
        endpoint: str | None = None,
        env: dict[str, str] | None = None,
        idle_ttl_s: int = 300,
        health_check_interval_s: int = 60,
        max_consecutive_failures: int = 3,
    ):
        # Validate and convert provider_id
        object.__setattr__(self, "provider_id", ProviderId(provider_id))

        # Validate and convert mode
        try:
            object.__setattr__(self, "mode", ProviderMode(mode))
        except ValueError:
            raise ValueError(f"Invalid provider mode: {mode}. Must be one of: subprocess, docker, remote")

        # Validate mode-specific configuration
        resolved_mode = ProviderMode(mode)

        if resolved_mode == ProviderMode.SUBPROCESS:
            if not command:
                raise ValueError("Subprocess mode requires 'command' configuration")
            object.__setattr__(self, "command", CommandLine.from_list(command))
            object.__setattr__(self, "image", None)
            object.__setattr__(self, "endpoint", None)
        elif resolved_mode == ProviderMode.DOCKER:
            if not image:
                raise ValueError("Docker mode requires 'image' configuration")
            object.__setattr__(self, "command", None)
            object.__setattr__(self, "image", DockerImage(image))
            object.__setattr__(self, "endpoint", None)
        elif resolved_mode == ProviderMode.REMOTE:
            if not endpoint:
                raise ValueError("Remote mode requires 'endpoint' configuration")
            object.__setattr__(self, "command", None)
            object.__setattr__(self, "image", None)
            object.__setattr__(self, "endpoint", Endpoint(endpoint))

        # Environment variables
        object.__setattr__(self, "env", EnvironmentVariables(env) if env else EnvironmentVariables())

        # Timing configuration
        object.__setattr__(self, "idle_ttl", IdleTTL(idle_ttl_s))
        object.__setattr__(self, "health_check_interval", HealthCheckInterval(health_check_interval_s))
        object.__setattr__(
            self,
            "max_consecutive_failures",
            MaxConsecutiveFailures(max_consecutive_failures),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        result = {
            "provider_id": str(self.provider_id),
            "mode": str(self.mode),
            "idle_ttl_s": self.idle_ttl.seconds,
            "health_check_interval_s": self.health_check_interval.seconds,
            "max_consecutive_failures": self.max_consecutive_failures.count,
        }

        if self.command:
            result["command"] = self.command.to_list()
        if self.image:
            result["image"] = str(self.image)
        if self.endpoint:
            result["endpoint"] = str(self.endpoint)
        if self.env and len(self.env) > 0:
            result["env"] = self.env.to_dict()

        return result


# --- Tool Arguments ---


class ToolArguments:
    """Validated tool invocation arguments.

    Rules:
    - Must be a dictionary
    - Size limited to prevent DoS
    - Keys must be strings
    """

    MAX_SIZE_BYTES = 1_000_000  # 1MB limit
    MAX_DEPTH = 10  # Maximum nesting depth

    def __init__(self, arguments: dict[str, Any]):
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a dictionary")

        self._validate_size(arguments)
        self._validate_structure(arguments)
        self._arguments = arguments

    def _validate_size(self, arguments: dict[str, Any]) -> None:
        """Validate arguments don't exceed size limit."""
        import json

        try:
            size = len(json.dumps(arguments))
            if size > self.MAX_SIZE_BYTES:
                raise ValueError(f"Tool arguments exceed maximum size ({size} > {self.MAX_SIZE_BYTES} bytes)")
        except (TypeError, ValueError) as e:
            if "size" not in str(e):
                raise ValueError(f"Tool arguments must be JSON-serializable: {e}")
            raise

    def _validate_structure(self, obj: Any, depth: int = 0) -> None:
        """Validate argument structure and depth."""
        if depth > self.MAX_DEPTH:
            raise ValueError(f"Tool arguments exceed maximum nesting depth ({self.MAX_DEPTH})")

        if isinstance(obj, dict):
            for key, value in obj.items():
                if not isinstance(key, str):
                    raise ValueError("Tool argument keys must be strings")
                self._validate_structure(value, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                self._validate_structure(item, depth + 1)

    @property
    def value(self) -> dict[str, Any]:
        """Get the validated arguments."""
        return self._arguments

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (returns a copy)."""
        return dict(self._arguments)

    def __getitem__(self, key: str) -> Any:
        return self._arguments[key]

    def __contains__(self, key: str) -> bool:
        return key in self._arguments

    def get(self, key: str, default: Any = None) -> Any:
        return self._arguments.get(key, default)


# --- Group-related Value Objects ---


class GroupId:
    """Unique identifier for a provider group.

    Rules:
    - Same rules as ProviderId
    - Non-empty string
    - Alphanumeric, hyphens, underscores only
    - Max 64 characters
    """

    _VALID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
    _MAX_LENGTH = 64

    def __init__(self, value: str):
        if not value:
            raise ValueError("GroupId cannot be empty")
        if len(value) > self._MAX_LENGTH:
            raise ValueError(f"GroupId cannot exceed {self._MAX_LENGTH} characters")
        if not self._VALID_PATTERN.match(value):
            raise ValueError("GroupId must contain only alphanumeric characters, hyphens, and underscores")
        self._value = value

    @property
    def value(self) -> str:
        return self._value

    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"GroupId('{self._value}')"

    def __eq__(self, other) -> bool:
        if isinstance(other, str):
            return self._value == other
        if not isinstance(other, GroupId):
            return False
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)


class MemberWeight:
    """Weight for a group member in weighted load balancing.

    Rules:
    - Positive integer (>= 1)
    - Max value: 100
    - Higher weight = more traffic
    """

    MIN_WEIGHT = 1
    MAX_WEIGHT = 100

    def __init__(self, value: int = 1):
        if not isinstance(value, int):
            raise ValueError("MemberWeight must be an integer")
        if value < self.MIN_WEIGHT:
            raise ValueError(f"MemberWeight must be at least {self.MIN_WEIGHT}")
        if value > self.MAX_WEIGHT:
            raise ValueError(f"MemberWeight cannot exceed {self.MAX_WEIGHT}")
        self._value = value

    @property
    def value(self) -> int:
        return self._value

    def __int__(self) -> int:
        return self._value

    def __str__(self) -> str:
        return str(self._value)

    def __repr__(self) -> str:
        return f"MemberWeight({self._value})"

    def __eq__(self, other) -> bool:
        if isinstance(other, int):
            return self._value == other
        if not isinstance(other, MemberWeight):
            return False
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __lt__(self, other) -> bool:
        if isinstance(other, int):
            return self._value < other
        if isinstance(other, MemberWeight):
            return self._value < other._value
        return NotImplemented


class MemberPriority:
    """Priority for a group member in priority-based selection.

    Rules:
    - Positive integer (>= 1)
    - Lower value = higher priority (1 is highest)
    - Max value: 100
    """

    MIN_PRIORITY = 1
    MAX_PRIORITY = 100

    def __init__(self, value: int = 1):
        if not isinstance(value, int):
            raise ValueError("MemberPriority must be an integer")
        if value < self.MIN_PRIORITY:
            raise ValueError(f"MemberPriority must be at least {self.MIN_PRIORITY}")
        if value > self.MAX_PRIORITY:
            raise ValueError(f"MemberPriority cannot exceed {self.MAX_PRIORITY}")
        self._value = value

    @property
    def value(self) -> int:
        return self._value

    def __int__(self) -> int:
        return self._value

    def __str__(self) -> str:
        return str(self._value)

    def __repr__(self) -> str:
        return f"MemberPriority({self._value})"

    def __eq__(self, other) -> bool:
        if isinstance(other, int):
            return self._value == other
        if not isinstance(other, MemberPriority):
            return False
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __lt__(self, other) -> bool:
        if isinstance(other, int):
            return self._value < other
        if isinstance(other, MemberPriority):
            return self._value < other._value
        return NotImplemented


# --- Multi-Tenancy Value Objects ---


@dataclass(frozen=True)
class TenantId:
    """Unique identifier for a tenant.

    Tenants represent teams or business units with isolated resources.
    TenantIds must follow Kubernetes naming conventions for compatibility.

    Attributes:
        value: Alphanumeric string starting with letter (1-63 chars)
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > 63:
            raise ValueError("TenantId must be 1-63 characters")
        if not self.value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("TenantId must be alphanumeric with - or _")
        if not self.value[0].isalpha():
            raise ValueError("TenantId must start with a letter")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class NamespaceId:
    """Unique identifier for a namespace within a tenant.

    Namespaces provide workload isolation within a tenant.
    Follows Kubernetes namespace naming rules.

    Attributes:
        value: Alphanumeric string with hyphens (1-63 chars)
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > 63:
            raise ValueError("NamespaceId must be 1-63 characters")
        if not self.value.replace("-", "").isalnum():
            raise ValueError("NamespaceId must be alphanumeric with -")
        if not self.value[0].isalnum() or not self.value[-1].isalnum():
            raise ValueError("NamespaceId must start and end with alphanumeric")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class CatalogItemId:
    """Unique identifier for a catalog item.

    Catalog items are provider templates that can be deployed.

    Attributes:
        value: Identifier string (1-128 characters)
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > 128:
            raise ValueError("CatalogItemId must be 1-128 characters")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ResourceScope:
    """Scope for resource access control.

    Defines hierarchical scope for authorization:
    - Organization (all tenants)
    - Tenant (specific tenant + all its namespaces)
    - Namespace (specific tenant + specific namespace)

    Attributes:
        organization_id: Optional organization identifier
        tenant_id: Optional tenant identifier
        namespace_id: Optional namespace identifier
    """

    organization_id: str | None = None
    tenant_id: str | None = None
    namespace_id: str | None = None

    def includes(self, other: "ResourceScope") -> bool:
        """Check if this scope includes another scope.

        Organization scope includes all.
        Tenant scope includes matching tenant.
        Namespace scope requires exact match.

        Args:
            other: Scope to check

        Returns:
            True if this scope includes the other scope
        """
        # Organization scope includes all
        if self.tenant_id is None and self.namespace_id is None:
            return True

        # Tenant scope includes matching tenant
        if self.tenant_id and other.tenant_id != self.tenant_id:
            return False

        # Namespace scope requires exact match
        if self.namespace_id and other.namespace_id != self.namespace_id:
            return False

        return True

    def __str__(self) -> str:
        if self.namespace_id:
            return f"namespace:{self.tenant_id}/{self.namespace_id}"
        if self.tenant_id:
            return f"tenant:{self.tenant_id}"
        return "organization"
