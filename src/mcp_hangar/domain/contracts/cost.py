"""Cost attribution contract.

Defines the interface for computing per-invocation cost from tool call metadata.
"""

from dataclasses import dataclass
from typing import Protocol

from ..value_objects.cost import CostRecord


@dataclass(frozen=True)
class InvocationContext:
    """Input data for cost computation, extracted from a tool invocation event."""

    mcp_server_id: str
    tool_name: str
    duration_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    tenant_id: str = ""
    correlation_id: str = ""


class ICostAttributor(Protocol):
    """Computes cost for a tool invocation based on configured pricing rules."""

    def compute_cost(self, context: InvocationContext) -> CostRecord:
        """Compute cost for the given invocation context.

        Returns a CostRecord with cost_cents=0 if no pricing rule matches.
        """
        ...


class NullCostAttributor:
    """No-op cost attributor. Returns zero-cost records."""

    def compute_cost(self, context: InvocationContext) -> CostRecord:
        return CostRecord(
            mcp_server_id=context.mcp_server_id,
            tool_name=context.tool_name,
            duration_ms=context.duration_ms,
            input_tokens=context.input_tokens,
            output_tokens=context.output_tokens,
            cost_cents=0,
            tenant_id=context.tenant_id,
            correlation_id=context.correlation_id,
        )
