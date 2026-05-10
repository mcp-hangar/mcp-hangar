"""IMutator contract and mutation value objects for SEP-1763 interceptor framework.

Defines the Mutator type from SEP-1763: components that transform tool
inputs/outputs with sequential priority-based execution ordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class MutationContext:
    """Input to a mutator invocation.

    Attributes:
        method: MCP method name (e.g. "tools/call").
        direction: Whether this is a request or response payload.
        payload: The JSON-serializable payload to potentially mutate.
        correlation_id: Request correlation ID for audit trail linkage.
    """

    method: str
    direction: Literal["request", "response"]
    payload: dict[str, Any]
    correlation_id: str

    def __post_init__(self) -> None:
        if not self.method:
            raise ValueError("method cannot be empty")
        if not self.correlation_id:
            raise ValueError("correlation_id cannot be empty")


@dataclass(frozen=True)
class MutationResult:
    """Output of a mutator invocation.

    Attributes:
        payload: The (possibly modified) payload.
        changed: True if the mutator produced a different payload.
        audit_only: Shadow mode flag; consumed by P2 audit pipeline.
    """

    payload: dict[str, Any]
    changed: bool
    audit_only: bool = False


_INT32_MIN = -2_147_483_648
_INT32_MAX = 2_147_483_647


@runtime_checkable
class IMutator(Protocol):
    """Contract for components that transform MCP payloads.

    Mutators are ordered by priority_hint ascending (lower runs first).
    Each mutator declares which MCP methods it applies to.
    """

    @property
    def priority_hint(self) -> int:
        """Sequential ordering. Lower runs first. Range: int32."""
        ...

    @property
    def applies_to(self) -> frozenset[str]:
        """MCP method names this mutator may modify."""
        ...

    def mutate(self, context: MutationContext) -> MutationResult:
        """Transform the payload.

        Args:
            context: Mutation input with method, direction, payload, correlation_id.

        Returns:
            MutationResult with the (possibly modified) payload.
        """
        ...
