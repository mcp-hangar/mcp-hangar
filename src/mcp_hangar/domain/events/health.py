# pyright: reportExplicitAny=false

"""Health-check, idle and resource events."""

from dataclasses import dataclass

from ..value_objects.compat import (
    resolve_legacy_mcp_server_id as _resolve_legacy_mcp_server_id,
)
from .base import DomainEvent


# Health Check Events


@dataclass(init=False)
class HealthCheckPassed(DomainEvent):
    """Published when a health check succeeds."""

    mcp_server_id: str
    duration_ms: float

    def __init__(self, mcp_server_id: str | None = None, duration_ms: float = 0.0, **kwargs: object):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.duration_ms = duration_ms
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


@dataclass(init=False)
class HealthCheckFailed(DomainEvent):
    """Published when a health check fails."""

    mcp_server_id: str
    consecutive_failures: int
    error_message: str

    def __init__(
        self,
        mcp_server_id: str | None = None,
        consecutive_failures: int = 0,
        error_message: str = "",
        **kwargs: object,
    ):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.consecutive_failures = consecutive_failures
        self.error_message = error_message
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

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
