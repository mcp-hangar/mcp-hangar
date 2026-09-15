# pyright: reportExplicitAny=false

"""Server lifecycle and circuit-breaker events."""

from dataclasses import dataclass

from ..value_objects.compat import accepts_legacy_provider_id
from .base import DomainEvent


# McpServer Lifecycle Events


@dataclass
class McpServerStarted(DomainEvent):
    """Published when a mcp_server successfully starts."""

    mcp_server_id: str
    mode: str  # subprocess, docker, remote
    tools_count: int
    startup_duration_ms: float


@dataclass
class McpServerStopped(DomainEvent):
    """Published when a mcp_server is stopped."""

    mcp_server_id: str
    reason: str


@dataclass
class McpServerDegraded(DomainEvent):
    """Published when a mcp_server enters degraded state."""

    mcp_server_id: str
    consecutive_failures: int
    total_failures: int
    reason: str


DEGRADED_BY_HEALTH_CHECKS = "health_check_failures"
"""The `McpServerDegraded.reason` of a server that failing health checks degraded.

Named because two places must agree on it: `McpServer.health_check()` emits it
in the same check as that check's `HealthCheckFailed`, and whoever counts
failures must then count only one of the two.
"""

STOPPED_BY_GIVING_UP = "max_retries_exceeded"
"""The `McpServerStopped.reason` of a server the recovery saga gave up on (#1360).

A give-up closes the connection, as a stop does, and is recorded as a stop, so
`mcp_hangar_mcp_server_stops_total` counts it apart from an idle reap or an
operator's stop. The move to DEAD is recorded right after it: a stop replays to
COLD, and the move that follows leaves the stream, and the state gauge, at DEAD.
The value is the one the stop counter already carried for a give-up.
"""


@dataclass
class McpServerStateChanged(DomainEvent):
    """Published when mcp_server state transitions."""

    mcp_server_id: str
    old_state: str
    new_state: str
    #: Why, when ``new_state`` is ``dead``: one of the ``DEAD_*`` constants in
    #: ``domain.model.mcp_server``. None otherwise, and in streams written
    #: before it existed (#1361).
    dead_reason: str | None = None


# Circuit Breaker Events


@accepts_legacy_provider_id
@dataclass
class CircuitBreakerStateChanged(DomainEvent):
    """Published when a circuit breaker transitions between states."""

    mcp_server_id: str
    old_state: str = ""  # closed, open, half_open
    new_state: str = ""  # closed, open, half_open
