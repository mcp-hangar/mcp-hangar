# pyright: reportExplicitAny=false

"""Interceptor events (MCP PR #2624 / SEP-1763)."""

from dataclasses import dataclass

from .base import DomainEvent


# Interceptor Events (MCP PR #2624 / SEP-1763 reconciliation)
# =============================================================================


@dataclass
class InterceptorInvoked(DomainEvent):
    """Published when ``interceptor/invoke`` dispatches a phase-aware hook.

    Reconciles Hangar's hook framework with MCP PR #2624: each invocation
    carries a Lifecycle Event and a wire-level phase (``request``/``response``)
    so subscribers can observe phase-aware delivery on both legs of a call.

    Attributes:
        interceptor: Name of the invoked interceptor (e.g. ``mcp-hangar-validator``).
        lifecycle_event: The intercepted Lifecycle Event (e.g. ``tools/call``).
        phase: Wire-level phase, one of ``request`` or ``response``.
        correlation_id: Correlation ID linking request/response legs.
        schema_version: Event schema version.
    """

    interceptor: str
    lifecycle_event: str
    phase: str
    correlation_id: str
    schema_version: int = 1
