"""Provider-related value objects.

Contains:
- ProviderState - lifecycle state machine
- ProviderMode - execution modes
- ProviderId - unique identifier
- ProviderConfig - complete configuration
- LoadBalancerStrategy, GroupState - group-related enums
- GroupId, MemberWeight, MemberPriority - group value objects
"""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any


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


# Import config classes for ProviderConfig
# This import is placed here (after all basic types are defined) to avoid circular imports
# between provider.py and config.py. The config module only depends on basic types,
# while ProviderConfig (below) depends on config classes.
from .config import (  # noqa: E402
    CommandLine,
    DockerImage,
    Endpoint,
    EnvironmentVariables,
    HealthCheckInterval,
    IdleTTL,
    MaxConsecutiveFailures,
)


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
        except ValueError as e:
            raise ValueError(f"Invalid provider mode: {mode}. Must be one of: subprocess, docker, remote") from e

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
