"""Provider health classification policy.

This module centralizes the logic that maps a Provider's state + health tracker
signals into a user-facing health classification.

Why this exists:
- Avoids duplicating "health status" mapping logic across query handlers / APIs.
- Keeps interpretation of state and failures as a domain-level policy.
- Allows the policy to evolve without touching CQRS read mapping.

This policy is intentionally small and pure (no I/O, no imports from infrastructure).

Usage (typical):
    from mcp_hangar.domain.policies.provider_health import classify_provider_health

    health_status = classify_provider_health(
        state=provider.state,
        consecutive_failures=provider.health.consecutive_failures,
    )

Or, if you already have a HealthTracker-like object:
    health_status = classify_provider_health_from_provider(provider)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..value_objects import HealthStatus, ProviderState


class _HealthView(Protocol):
    """Minimal health-tracker view required by the policy.

    Defines the interface for accessing health metrics from any
    health-tracker-like object.
    """

    @property
    def consecutive_failures(self) -> int:
        """Get the count of consecutive failures."""
        ...


class _ProviderView(Protocol):
    """Minimal provider view required by the policy.

    Defines the interface for accessing provider state and health
    from any provider-like object.
    """

    @property
    def state(self) -> ProviderState:
        """Get the current provider state."""
        ...

    @property
    def health(self) -> _HealthView:
        """Get the health tracker view."""
        ...


def _normalize_state(state: Any) -> ProviderState:
    """Convert a loose/legacy state representation to ProviderState."""
    if isinstance(state, ProviderState):
        return state

    # Some call sites may pass enum-like objects with `.value`
    value = getattr(state, "value", None)
    if value is not None:
        state_str = str(value).lower()
    else:
        state_str = str(state).lower()

    for s in ProviderState:
        if s.value == state_str:
            return s

    # If unknown, treat as DEAD from a health classification standpoint
    # (conservative default).
    return ProviderState.DEAD


@dataclass(frozen=True)
class ProviderHealthClassification:
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


def classify_provider_health(
    *,
    state: Any,
    consecutive_failures: int = 0,
) -> ProviderHealthClassification:
    """Classify provider health from state and failure count.

    Rules (current):
    - READY + 0 failures -> HEALTHY
    - READY + >0 failures -> DEGRADED
    - DEGRADED -> DEGRADED
    - DEAD -> UNHEALTHY
    - COLD / INITIALIZING -> UNKNOWN

    Notes:
    - This is a *classification*, not the same as "can accept requests".
      That rule is handled by ProviderState.can_accept_requests and other domain logic.
    """
    st = _normalize_state(state)
    failures = int(consecutive_failures or 0)

    if st == ProviderState.READY:
        if failures <= 0:
            return ProviderHealthClassification(
                status=HealthStatus.HEALTHY,
                reason="ready_no_failures",
                consecutive_failures=failures,
            )
        return ProviderHealthClassification(
            status=HealthStatus.DEGRADED,
            reason="ready_with_failures",
            consecutive_failures=failures,
        )

    if st == ProviderState.DEGRADED:
        return ProviderHealthClassification(
            status=HealthStatus.DEGRADED,
            reason="provider_state_degraded",
            consecutive_failures=failures,
        )

    if st == ProviderState.DEAD:
        return ProviderHealthClassification(
            status=HealthStatus.UNHEALTHY,
            reason="provider_state_dead",
            consecutive_failures=failures,
        )

    if st in (ProviderState.COLD, ProviderState.INITIALIZING):
        return ProviderHealthClassification(
            status=HealthStatus.UNKNOWN,
            reason=f"provider_state_{st.value}",
            consecutive_failures=failures,
        )

    # Fallback (shouldn't happen due to normalization)
    return ProviderHealthClassification(
        status=HealthStatus.UNKNOWN,
        reason="unknown_state",
        consecutive_failures=failures,
    )


def classify_provider_health_from_provider(
    provider: _ProviderView,
) -> ProviderHealthClassification:
    """Convenience wrapper to classify health from a provider-like object."""
    return classify_provider_health(
        state=provider.state,
        consecutive_failures=provider.health.consecutive_failures,
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
    return classify_provider_health(
        state=state,
        consecutive_failures=consecutive_failures,
    ).status.value
