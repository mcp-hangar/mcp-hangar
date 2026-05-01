"""Default cost attribution implementation.

Computes per-invocation cost based on configurable pricing rules
with specificity-based rule matching (exact > wildcard).
"""

from ..domain.contracts.cost import InvocationContext
from ..domain.value_objects.cost import CostModel, CostRecord, PricingRule


class DefaultCostAttributor:
    """Computes cost using a list of pricing rules with specificity matching.

    Rule matching priority (first match wins):
    1. Exact mcp_server_id + exact tool_name
    2. Exact mcp_server_id + wildcard tool_name
    3. Wildcard mcp_server_id + exact tool_name
    4. Wildcard mcp_server_id + wildcard tool_name (default)

    If no rule matches, returns cost_cents=0.
    """

    def __init__(self, rules: list[PricingRule] | None = None) -> None:
        self._rules = rules or []

    def compute_cost(self, context: InvocationContext) -> CostRecord:
        rule = self._match_rule(context.mcp_server_id, context.tool_name)
        if rule is None:
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

        cost_cents = self._calculate(rule, context)
        return CostRecord(
            mcp_server_id=context.mcp_server_id,
            tool_name=context.tool_name,
            duration_ms=context.duration_ms,
            input_tokens=context.input_tokens,
            output_tokens=context.output_tokens,
            cost_cents=cost_cents,
            currency="USD",
            cost_model=rule.model,
            tenant_id=context.tenant_id,
            correlation_id=context.correlation_id,
        )

    def _match_rule(self, mcp_server_id: str, tool_name: str) -> PricingRule | None:
        # Specificity tiers: exact+exact > exact+wild > wild+exact > wild+wild
        best: PricingRule | None = None
        best_score = -1

        for rule in self._rules:
            server_match = rule.mcp_server_id == mcp_server_id or rule.mcp_server_id == "*"
            tool_match = rule.tool_name == tool_name or rule.tool_name == "*"
            if not (server_match and tool_match):
                continue

            score = 0
            if rule.mcp_server_id != "*":
                score += 2
            if rule.tool_name != "*":
                score += 1

            if score > best_score:
                best = rule
                best_score = score

        return best

    def _calculate(self, rule: PricingRule, context: InvocationContext) -> int:
        if rule.model == CostModel.FIXED:
            return rule.fixed_cost_cents

        if rule.model == CostModel.TOKEN:
            return int(
                context.input_tokens * rule.cost_per_input_token_cents
                + context.output_tokens * rule.cost_per_output_token_cents
            )

        if rule.model == CostModel.DURATION:
            return int(context.duration_ms * rule.cost_per_ms_cents)

        # CostModel.COMPOSITE: sum of all components
        return int(
            context.input_tokens * rule.cost_per_input_token_cents
            + context.output_tokens * rule.cost_per_output_token_cents
            + context.duration_ms * rule.cost_per_ms_cents
            + rule.fixed_cost_cents
        )
