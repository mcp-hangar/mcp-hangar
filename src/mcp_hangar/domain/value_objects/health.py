"""Health-related value objects.

Contains:
- HealthStatus - health classification enum
- HealthCheckInterval - timing configuration
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .mcp_server import McpServerState


class HealthStatus(Enum):
    """Health status for mcp_servers.

    Represents the externally visible health classification of a mcp_server.

    Attributes:
        HEALTHY: McpServer is fully operational with no recent failures.
        DEGRADED: McpServer is operational but has experienced recent failures.
        UNHEALTHY: McpServer is not operational or has exceeded failure threshold.
        UNKNOWN: McpServer health cannot be determined (e.g., not started).
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        """Return the string representation of the status."""
        return self.value

    @classmethod
    def from_state(cls, state: McpServerState, consecutive_failures: int = 0) -> HealthStatus:
        """Derive health status from mcp_server state and failure count.

        Args:
            state: The current mcp_server state.
            consecutive_failures: Number of consecutive failures (default: 0).

        Returns:
            The derived HealthStatus based on state and failures.

        Example:
            >>> from mcp_hangar.domain.value_objects import McpServerState, HealthStatus
            >>> HealthStatus.from_state(McpServerState.READY, 0)
            <HealthStatus.HEALTHY: 'healthy'>
        """
        # Import here to avoid circular import
        from .mcp_server import McpServerState  # noqa: N817

        if state == McpServerState.READY:
            if consecutive_failures == 0:
                return cls.HEALTHY
            return cls.DEGRADED
        elif state == McpServerState.DEGRADED:
            return cls.UNHEALTHY
        elif state == McpServerState.COLD:
            return cls.UNKNOWN
        else:
            return cls.UNHEALTHY


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
