"""
Value Objects for the MCP Registry domain.

Value objects are immutable, validated domain primitives that encapsulate
business rules and prevent invalid states. They replace primitive obsession
with strongly-typed domain concepts.
"""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import uuid

# --- Enums ---


class ProviderState(Enum):
    """Provider lifecycle states.

    State machine transitions:
    - COLD -> INITIALIZING (on start)
    - INITIALIZING -> READY (on success) | DEAD (on failure) | DEGRADED (on max failures)
    - READY -> COLD (on shutdown) | DEAD (on client death) | DEGRADED (on health failures)
    - DEGRADED -> INITIALIZING (on retry) | COLD (on shutdown)
    - DEAD -> INITIALIZING (on retry) | DEGRADED (on max failures)
    """

    COLD = "cold"
    INITIALIZING = "initializing"
    READY = "ready"
    DEGRADED = "degraded"
    DEAD = "dead"

    def __str__(self) -> str:
        return self.value

    @property
    def can_accept_requests(self) -> bool:
        """Check if provider can accept tool invocation requests."""
        return self == ProviderState.READY

    @property
    def can_start(self) -> bool:
        """Check if provider can be started from this state."""
        return self in (ProviderState.COLD, ProviderState.DEAD, ProviderState.DEGRADED)


class HealthStatus(Enum):
    """Health status for providers."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_state(cls, state: ProviderState, consecutive_failures: int = 0) -> "HealthStatus":
        """Derive health status from provider state and failures."""
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
    REMOTE = "remote"
    GROUP = "group"  # Provider group with load balancing

    def __str__(self) -> str:
        return self.value


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

    Rules:
    - Non-empty string
    - Alphanumeric, hyphens, underscores only
    - Max 64 characters
    """

    _VALID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
    _MAX_LENGTH = 64

    def __init__(self, value: str):
        if not value:
            raise ValueError("ProviderId cannot be empty")
        if len(value) > self._MAX_LENGTH:
            raise ValueError(f"ProviderId cannot exceed {self._MAX_LENGTH} characters")
        if not self._VALID_PATTERN.match(value):
            raise ValueError("ProviderId must contain only alphanumeric characters, hyphens, and underscores")
        self._value = value

    @property
    def value(self) -> str:
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

    Rules:
    - Non-empty string
    - Alphanumeric, hyphens, underscores, dots allowed (for namespaced tools)
    - Max 128 characters
    """

    _VALID_PATTERN = re.compile(r"^[a-zA-Z0-9_.\-]+$")
    _MAX_LENGTH = 128

    def __init__(self, value: str):
        if not value:
            raise ValueError("ToolName cannot be empty")
        if len(value) > self._MAX_LENGTH:
            raise ValueError(f"ToolName cannot exceed {self._MAX_LENGTH} characters")
        if not self._VALID_PATTERN.match(value):
            raise ValueError("ToolName must contain only alphanumeric characters, hyphens, underscores, and dots")
        self._value = value

    @property
    def value(self) -> str:
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

    def __init__(self, value: Optional[str] = None):
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
    def from_list(cls, command_list: List[str]) -> "CommandLine":
        """Create from a list of strings."""
        if not command_list:
            raise ValueError("Command list cannot be empty")
        return cls(command_list[0], *command_list[1:])

    def to_list(self) -> List[str]:
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

    variables: Dict[str, str]

    def __init__(self, variables: Optional[Dict[str, str]] = None):
        vars_dict = variables or {}

        # Validate keys
        for key in vars_dict.keys():
            if not key:
                raise ValueError("Environment variable key cannot be empty")
            if not isinstance(key, str):
                raise ValueError("Environment variable key must be a string")

        # Create immutable copy
        object.__setattr__(self, "variables", dict(vars_dict))

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get environment variable value."""
        return self.variables.get(key, default)

    def __getitem__(self, key: str) -> str:
        return self.variables[key]

    def __contains__(self, key: str) -> bool:
        return key in self.variables

    def __len__(self) -> int:
        return len(self.variables)

    def to_dict(self) -> Dict[str, str]:
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


# --- Provider Configuration ---


@dataclass(frozen=True)
class ProviderConfig:
    """Complete configuration for a provider.

    Encapsulates all configuration options in a validated, immutable object.
    """

    provider_id: ProviderId
    mode: ProviderMode
    command: Optional[CommandLine] = None
    image: Optional[DockerImage] = None
    endpoint: Optional[Endpoint] = None
    env: Optional[EnvironmentVariables] = None
    idle_ttl: IdleTTL = None
    health_check_interval: HealthCheckInterval = None
    max_consecutive_failures: MaxConsecutiveFailures = None

    def __init__(
        self,
        provider_id: str,
        mode: str,
        command: Optional[List[str]] = None,
        image: Optional[str] = None,
        endpoint: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
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

    def to_dict(self) -> Dict[str, Any]:
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

    def __init__(self, arguments: Dict[str, Any]):
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a dictionary")

        self._validate_size(arguments)
        self._validate_structure(arguments)
        self._arguments = arguments

    def _validate_size(self, arguments: Dict[str, Any]) -> None:
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
    def value(self) -> Dict[str, Any]:
        """Get the validated arguments."""
        return self._arguments

    def to_dict(self) -> Dict[str, Any]:
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
