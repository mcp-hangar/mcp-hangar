"""Cost attribution value objects.

Domain primitives for per-invocation cost tracking and attribution.
"""

from dataclasses import dataclass
from enum import Enum


class CostModel(Enum):
    """Pricing model used for cost attribution."""

    TOKEN = "token"
    DURATION = "duration"
    FIXED = "fixed"
    COMPOSITE = "composite"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class CostRecord:
    """Immutable record of computed cost for a single tool invocation.

    Attributes:
        mcp_server_id: Server that handled the call.
        tool_name: Tool that was invoked.
        duration_ms: Call duration in milliseconds.
        input_tokens: Input tokens consumed (0 if not applicable).
        output_tokens: Output tokens produced (0 if not applicable).
        cost_cents: Computed cost in hundredths of a cent (integer for precision).
        currency: ISO 4217 currency code.
        cost_model: Pricing model used for attribution.
        tenant_id: Tenant owning this invocation (empty string if single-tenant).
        correlation_id: Correlation ID linking to the original invocation event.
    """

    mcp_server_id: str
    tool_name: str
    duration_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_cents: int = 0
    currency: str = "USD"
    cost_model: CostModel = CostModel.DURATION
    tenant_id: str = ""
    correlation_id: str = ""


@dataclass(frozen=True)
class PricingRule:
    """Configuration for a single pricing rule.

    Attributes:
        mcp_server_id: Server this rule applies to ("*" for default).
        tool_name: Tool this rule applies to ("*" for all tools on server).
        cost_per_input_token_cents: Cost per input token in hundredths of a cent.
        cost_per_output_token_cents: Cost per output token in hundredths of a cent.
        cost_per_ms_cents: Cost per millisecond of duration in hundredths of a cent.
        fixed_cost_cents: Fixed per-invocation cost in hundredths of a cent.
        model: Pricing model to apply.
    """

    mcp_server_id: str = "*"
    tool_name: str = "*"
    cost_per_input_token_cents: float = 0.0
    cost_per_output_token_cents: float = 0.0
    cost_per_ms_cents: float = 0.0
    fixed_cost_cents: int = 0
    model: CostModel = CostModel.DURATION
