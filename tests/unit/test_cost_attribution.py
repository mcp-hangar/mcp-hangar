"""Unit tests for cost attribution (B-12)."""

from unittest.mock import MagicMock

from mcp_hangar.domain.contracts.cost import InvocationContext, NullCostAttributor
from mcp_hangar.domain.value_objects.cost import CostModel, CostRecord, PricingRule
from mcp_hangar.infrastructure.cost_attributor import DefaultCostAttributor


class TestCostRecord:
    def test_frozen_dataclass(self) -> None:
        record = CostRecord(mcp_server_id="math", tool_name="add", duration_ms=100.0)
        assert record.cost_cents == 0
        assert record.currency == "USD"
        assert record.cost_model == CostModel.DURATION

    def test_custom_values(self) -> None:
        record = CostRecord(
            mcp_server_id="llm",
            tool_name="gen",
            duration_ms=500.0,
            input_tokens=1000,
            output_tokens=500,
            cost_cents=250,
            cost_model=CostModel.TOKEN,
            tenant_id="t1",
        )
        assert record.cost_cents == 250
        assert record.input_tokens == 1000
        assert record.tenant_id == "t1"


class TestPricingRule:
    def test_defaults(self) -> None:
        rule = PricingRule()
        assert rule.mcp_server_id == "*"
        assert rule.tool_name == "*"
        assert rule.model == CostModel.DURATION


class TestNullCostAttributor:
    def test_returns_zero_cost(self) -> None:
        attributor = NullCostAttributor()
        ctx = InvocationContext(mcp_server_id="math", tool_name="add", duration_ms=100.0)
        result = attributor.compute_cost(ctx)
        assert result.cost_cents == 0
        assert result.mcp_server_id == "math"


class TestDefaultCostAttributor:
    def test_no_rules_returns_zero_cost(self) -> None:
        attributor = DefaultCostAttributor(rules=[])
        ctx = InvocationContext(mcp_server_id="math", tool_name="add", duration_ms=100.0)
        result = attributor.compute_cost(ctx)
        assert result.cost_cents == 0

    def test_duration_pricing(self) -> None:
        rules = [PricingRule(cost_per_ms_cents=0.1, model=CostModel.DURATION)]
        attributor = DefaultCostAttributor(rules=rules)
        ctx = InvocationContext(mcp_server_id="math", tool_name="add", duration_ms=100.0)
        result = attributor.compute_cost(ctx)
        assert result.cost_cents == 10
        assert result.cost_model == CostModel.DURATION

    def test_token_pricing(self) -> None:
        rules = [PricingRule(
            cost_per_input_token_cents=0.03,
            cost_per_output_token_cents=0.06,
            model=CostModel.TOKEN,
        )]
        attributor = DefaultCostAttributor(rules=rules)
        ctx = InvocationContext(
            mcp_server_id="llm", tool_name="gen",
            duration_ms=500.0, input_tokens=1000, output_tokens=500,
        )
        result = attributor.compute_cost(ctx)
        assert result.cost_cents == int(1000 * 0.03 + 500 * 0.06)
        assert result.cost_model == CostModel.TOKEN

    def test_fixed_pricing(self) -> None:
        rules = [PricingRule(fixed_cost_cents=50, model=CostModel.FIXED)]
        attributor = DefaultCostAttributor(rules=rules)
        ctx = InvocationContext(mcp_server_id="x", tool_name="y", duration_ms=999.0)
        result = attributor.compute_cost(ctx)
        assert result.cost_cents == 50

    def test_composite_pricing(self) -> None:
        rules = [PricingRule(
            cost_per_input_token_cents=0.01,
            cost_per_output_token_cents=0.02,
            cost_per_ms_cents=0.001,
            fixed_cost_cents=5,
            model=CostModel.COMPOSITE,
        )]
        attributor = DefaultCostAttributor(rules=rules)
        ctx = InvocationContext(
            mcp_server_id="x", tool_name="y",
            duration_ms=1000.0, input_tokens=100, output_tokens=50,
        )
        result = attributor.compute_cost(ctx)
        expected = int(100 * 0.01 + 50 * 0.02 + 1000 * 0.001 + 5)
        assert result.cost_cents == expected

    def test_specificity_exact_server_exact_tool_wins(self) -> None:
        rules = [
            PricingRule(mcp_server_id="*", tool_name="*", fixed_cost_cents=1, model=CostModel.FIXED),
            PricingRule(mcp_server_id="llm", tool_name="gen", fixed_cost_cents=99, model=CostModel.FIXED),
        ]
        attributor = DefaultCostAttributor(rules=rules)
        ctx = InvocationContext(mcp_server_id="llm", tool_name="gen", duration_ms=10.0)
        result = attributor.compute_cost(ctx)
        assert result.cost_cents == 99

    def test_specificity_exact_server_wildcard_tool(self) -> None:
        rules = [
            PricingRule(mcp_server_id="*", tool_name="*", fixed_cost_cents=1, model=CostModel.FIXED),
            PricingRule(mcp_server_id="llm", tool_name="*", fixed_cost_cents=50, model=CostModel.FIXED),
        ]
        attributor = DefaultCostAttributor(rules=rules)
        ctx = InvocationContext(mcp_server_id="llm", tool_name="anything", duration_ms=10.0)
        result = attributor.compute_cost(ctx)
        assert result.cost_cents == 50

    def test_no_matching_rule(self) -> None:
        rules = [PricingRule(mcp_server_id="llm", tool_name="gen", fixed_cost_cents=99, model=CostModel.FIXED)]
        attributor = DefaultCostAttributor(rules=rules)
        ctx = InvocationContext(mcp_server_id="other", tool_name="x", duration_ms=10.0)
        result = attributor.compute_cost(ctx)
        assert result.cost_cents == 0


class TestCostAttributionEventHandler:
    def test_computes_and_publishes_cost_event(self) -> None:
        from mcp_hangar.application.event_handlers.cost_handler import CostAttributionEventHandler
        from mcp_hangar.domain.events import ToolInvocationCompleted

        rules = [PricingRule(fixed_cost_cents=42, model=CostModel.FIXED)]
        attributor = DefaultCostAttributor(rules=rules)
        event_bus = MagicMock()

        handler = CostAttributionEventHandler(cost_attributor=attributor, event_bus=event_bus)

        event = ToolInvocationCompleted(
            mcp_server_id="math",
            tool_name="add",
            correlation_id="c1",
            duration_ms=100.0,
            result_size_bytes=50,
        )
        handler.handle(event)

        event_bus.publish.assert_called_once()
        published_events = event_bus.publish.call_args[0][0]
        assert len(published_events) == 1
        assert published_events[0].total_cost == str(42 / 100.0)

    def test_skips_zero_cost(self) -> None:
        from mcp_hangar.application.event_handlers.cost_handler import CostAttributionEventHandler
        from mcp_hangar.domain.events import ToolInvocationCompleted

        attributor = NullCostAttributor()
        event_bus = MagicMock()

        handler = CostAttributionEventHandler(cost_attributor=attributor, event_bus=event_bus)
        event = ToolInvocationCompleted(
            mcp_server_id="math", tool_name="add",
            correlation_id="c1", duration_ms=10.0, result_size_bytes=0,
        )
        handler.handle(event)
        event_bus.publish.assert_not_called()

    def test_ignores_non_tool_events(self) -> None:
        from mcp_hangar.application.event_handlers.cost_handler import CostAttributionEventHandler
        from mcp_hangar.domain.events import McpServerStateChanged

        handler = CostAttributionEventHandler()
        event = McpServerStateChanged(mcp_server_id="x", old_state="COLD", new_state="READY")
        handler.handle(event)
