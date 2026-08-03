# pyright: reportExplicitAny=false

"""Health-check, idle and resource events."""

from dataclasses import dataclass

from ..value_objects.compat import accepts_legacy_provider_id
from .base import DomainEvent


# Health Check Events


@accepts_legacy_provider_id
@dataclass
class HealthCheckPassed(DomainEvent):
    """Published when a health check succeeds."""

    mcp_server_id: str
    duration_ms: float = 0.0

    def __post_init__(self):
        super().__init__()


@accepts_legacy_provider_id
@dataclass
class HealthCheckFailed(DomainEvent):
    """Published when a health check fails."""

    mcp_server_id: str
    consecutive_failures: int = 0
    error_message: str = ""

    def __post_init__(self):
        super().__init__()


# Resource Management Events


@dataclass
class McpServerIdleDetected(DomainEvent):
    """Published when a mcp_server is detected as idle."""

    mcp_server_id: str
    idle_duration_s: float
    last_used_at: float

    def __post_init__(self):
        super().__init__()


# McpServer Group Events are defined in mcp_hangar.domain.model.mcp_server_group
# to avoid circular imports. Re-export them here for convenience.
# Import at runtime only when needed.
