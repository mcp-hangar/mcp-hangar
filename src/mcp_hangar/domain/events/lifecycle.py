# pyright: reportExplicitAny=false

"""Server lifecycle and circuit-breaker events."""

from dataclasses import dataclass

from ..value_objects.compat import (
    resolve_legacy_mcp_server_id as _resolve_legacy_mcp_server_id,
)
from .base import DomainEvent


# McpServer Lifecycle Events


@dataclass
class McpServerStarted(DomainEvent):
    """Published when a mcp_server successfully starts."""

    mcp_server_id: str
    mode: str  # subprocess, docker, remote
    tools_count: int
    startup_duration_ms: float

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerStopped(DomainEvent):
    """Published when a mcp_server is stopped."""

    mcp_server_id: str
    reason: str

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerDegraded(DomainEvent):
    """Published when a mcp_server enters degraded state."""

    mcp_server_id: str
    consecutive_failures: int
    total_failures: int
    reason: str

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerStateChanged(DomainEvent):
    """Published when mcp_server state transitions."""

    mcp_server_id: str
    old_state: str
    new_state: str

    def __post_init__(self):
        super().__init__()


# Circuit Breaker Events


@dataclass(init=False)
class CircuitBreakerStateChanged(DomainEvent):
    """Published when a circuit breaker transitions between states."""

    mcp_server_id: str
    old_state: str  # closed, open, half_open
    new_state: str  # closed, open, half_open

    def __init__(self, mcp_server_id: str | None = None, old_state: str = "", new_state: str = "", **kwargs: object):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.old_state = old_state
        self.new_state = new_state
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()
