"""Domain events for the MCP Registry.

Events capture important business occurrences and allow decoupled reactions.
"""

from abc import ABC
from dataclasses import dataclass, field
import time
from typing import Any, Dict
import uuid


class DomainEvent(ABC):
    """
    Base class for all domain events.

    Note: Not a dataclass to avoid inheritance issues.
    Subclasses should be dataclasses.
    """

    def __init__(self):
        self.event_id: str = str(uuid.uuid4())
        self.occurred_at: float = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for serialization."""
        return {"event_type": self.__class__.__name__, **self.__dict__}


# Provider Lifecycle Events


@dataclass
class ProviderStarted(DomainEvent):
    """Published when a provider successfully starts."""

    provider_id: str
    mode: str  # subprocess, docker, remote
    tools_count: int
    startup_duration_ms: float

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderStopped(DomainEvent):
    """Published when a provider stops."""

    provider_id: str
    reason: str  # "shutdown", "idle", "error", "degraded"

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderDegraded(DomainEvent):
    """Published when a provider enters degraded state."""

    provider_id: str
    consecutive_failures: int
    total_failures: int
    reason: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderStateChanged(DomainEvent):
    """Published when provider state transitions."""

    provider_id: str
    old_state: str
    new_state: str

    def __post_init__(self):
        super().__init__()


# Tool Invocation Events


@dataclass
class ToolInvocationRequested(DomainEvent):
    """Published when a tool invocation is requested."""

    provider_id: str
    tool_name: str
    correlation_id: str
    arguments: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolInvocationCompleted(DomainEvent):
    """Published when a tool invocation completes successfully."""

    provider_id: str
    tool_name: str
    correlation_id: str
    duration_ms: float
    result_size_bytes: int = 0

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolInvocationFailed(DomainEvent):
    """Published when a tool invocation fails."""

    provider_id: str
    tool_name: str
    correlation_id: str
    error_message: str
    error_type: str

    def __post_init__(self):
        super().__init__()


# Health Check Events


@dataclass
class HealthCheckPassed(DomainEvent):
    """Published when a health check succeeds."""

    provider_id: str
    duration_ms: float

    def __post_init__(self):
        super().__init__()


@dataclass
class HealthCheckFailed(DomainEvent):
    """Published when a health check fails."""

    provider_id: str
    consecutive_failures: int
    error_message: str

    def __post_init__(self):
        super().__init__()


# Resource Management Events


@dataclass
class ProviderIdleDetected(DomainEvent):
    """Published when a provider is detected as idle."""

    provider_id: str
    idle_duration_s: float
    last_used_at: float

    def __post_init__(self):
        super().__init__()


# Provider Group Events are defined in mcp_hangar.domain.model.provider_group
# to avoid circular imports. Re-export them here for convenience.
# Import at runtime only when needed.


# Discovery Events


@dataclass
class ProviderDiscovered(DomainEvent):
    """Published when a new provider is discovered."""

    provider_name: str
    source_type: str
    mode: str
    fingerprint: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderDiscoveryLost(DomainEvent):
    """Published when a previously discovered provider is no longer found."""

    provider_name: str
    source_type: str
    reason: str  # "ttl_expired", "source_removed", etc.

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderDiscoveryConfigChanged(DomainEvent):
    """Published when discovered provider configuration changes."""

    provider_name: str
    source_type: str
    old_fingerprint: str
    new_fingerprint: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderQuarantined(DomainEvent):
    """Published when a discovered provider is quarantined."""

    provider_name: str
    source_type: str
    reason: str
    validation_result: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderApproved(DomainEvent):
    """Published when a quarantined provider is approved."""

    provider_name: str
    source_type: str
    approved_by: str  # "manual" or "auto"

    def __post_init__(self):
        super().__init__()


@dataclass
class DiscoveryCycleCompleted(DomainEvent):
    """Published when a discovery cycle completes."""

    discovered_count: int
    registered_count: int
    deregistered_count: int
    quarantined_count: int
    error_count: int
    duration_ms: float

    def __post_init__(self):
        super().__init__()


@dataclass
class DiscoverySourceHealthChanged(DomainEvent):
    """Published when a discovery source health status changes."""

    source_type: str
    is_healthy: bool
    error_message: str | None = None

    def __post_init__(self):
        super().__init__()
