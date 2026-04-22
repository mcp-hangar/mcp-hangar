"""McpServer health classification policy.

This module centralizes the logic that maps a McpServer's state + health tracker
signals into a user-facing health classification.

Why this exists:
- Avoids duplicating "health status" mapping logic across query handlers / APIs.
- Keeps interpretation of state and failures as a domain-level policy.
- Allows the policy to evolve without touching CQRS read mapping.

This policy is intentionally small and pure (no I/O, no imports from infrastructure).

Usage (typical):
    from mcp_hangar.domain.policies.mcp_server_health import classify_mcp_server_health

    health_status = classify_mcp_server_health(
        state=mcp_server.state,
        consecutive_failures=mcp_server.health.consecutive_failures,
    )

Or, if you already have a HealthTracker-like object:
    health_status = classify_mcp_server_health_from_mcp_server(mcp_server)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..value_objects import HealthStatus, McpServerState


class _HealthView(Protocol):
    """Minimal health-tracker view required by the policy.

    Defines the interface for accessing health metrics from any
    health-tracker-like object.
    """

    @property
    def consecutive_failures(self) -> int:
        """Get the count of consecutive failures."""
        ...


class _McpServerView(Protocol):
    """Minimal mcp_server view required by the policy.

    Defines the interface for accessing mcp_server state and health
    from any mcp_server-like object.
    """

    @property
    def state(self) -> McpServerState:
        """Get the current mcp_server state."""
        ...

    @property
    def health(self) -> _HealthView:
        """Get the health tracker view."""
        ...


def _normalize_state(state: Any) -> McpServerState:
    """Convert a loose/legacy state representation to McpServerState."""
    if isinstance(state, McpServerState):
        return state

    # Some call sites may pass enum-like objects with `.value`
    value = getattr(state, "value", None)
    if value is not None:
        state_str = str(value).lower()
    else:
        state_str = str(state).lower()

    for s in McpServerState:
        if s.value == state_str:
            return s

    # If unknown, treat as DEAD from a health classification standpoint
    # (conservative default).
    return McpServerState.DEAD


@dataclass(frozen=True)
class McpServerHealthClassification:
    """Result of applying the classification policy."""

    status: HealthStatus
    reason: str
    consecutive_failures: int

    def to_dict(self) -> dict:
        return {
            "status": str(self.status),
            "reason": self.reason,
            "consecutive_failures": self.consecutive_failures,
        }


def classify_mcp_server_health(
    *,
    state: Any,
    consecutive_failures: int = 0,
) -> McpServerHealthClassification:
    """Classify mcp_server health from state and failure count.

    Rules (current):
    - READY + 0 failures -> HEALTHY
    - READY + >0 failures -> DEGRADED
    - DEGRADED -> DEGRADED
    - DEAD -> UNHEALTHY
    - COLD / INITIALIZING -> UNKNOWN

    Notes:
    - This is a *classification*, not the same as "can accept requests".
      That rule is handled by McpServerState.can_accept_requests and other domain logic.
    """
    st = _normalize_state(state)
    failures = int(consecutive_failures or 0)

    if st == McpServerState.READY:
        if failures <= 0:
            return McpServerHealthClassification(
                status=HealthStatus.HEALTHY,
                reason="ready_no_failures",
                consecutive_failures=failures,
            )
        return McpServerHealthClassification(
            status=HealthStatus.DEGRADED,
            reason="ready_with_failures",
            consecutive_failures=failures,
        )

    if st == McpServerState.DEGRADED:
        return McpServerHealthClassification(
            status=HealthStatus.DEGRADED,
            reason="mcp_server_state_degraded",
            consecutive_failures=failures,
        )

    if st == McpServerState.DEAD:
        return McpServerHealthClassification(
            status=HealthStatus.UNHEALTHY,
            reason="mcp_server_state_dead",
            consecutive_failures=failures,
        )

    if st in (McpServerState.COLD, McpServerState.INITIALIZING):
        return McpServerHealthClassification(
            status=HealthStatus.UNKNOWN,
            reason=f"mcp_server_state_{st.value}",
            consecutive_failures=failures,
        )

    # Fallback (shouldn't happen due to normalization)
    return McpServerHealthClassification(
        status=HealthStatus.UNKNOWN,
        reason="unknown_state",
        consecutive_failures=failures,
    )


def classify_mcp_server_health_from_mcp_server(
    mcp_server: _McpServerView,
) -> McpServerHealthClassification:
    """Convenience wrapper to classify health from a mcp_server-like object."""
    return classify_mcp_server_health(
        state=mcp_server.state,
        consecutive_failures=mcp_server.health.consecutive_failures,
    )


def to_health_status_string(
    *,
    state: Any,
    consecutive_failures: int = 0,
) -> str:
    """Legacy helper: return the `HealthStatus.value` string.

    This exists to minimize changes in read model mapping code while still routing
    logic through a single policy.
    """
    return classify_mcp_server_health(
        state=state,
        consecutive_failures=consecutive_failures,
    ).status.value


# legacy aliases
ProviderHealthClassification = McpServerHealthClassification
classify_provider_health = classify_mcp_server_health
classify_provider_health_from_provider = classify_mcp_server_health_from_mcp_server
