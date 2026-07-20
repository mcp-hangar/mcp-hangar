"""The cost handler emits the Prometheus cost metrics (observability audit)."""

from __future__ import annotations

from mcp_hangar import metrics as m
from mcp_hangar.application.event_handlers.cost_handler import CostAttributionEventHandler
from mcp_hangar.domain.contracts.cost import InvocationContext
from mcp_hangar.domain.events import ToolInvocationCompleted
from mcp_hangar.domain.value_objects.cost import CostRecord


class _FixedAttributor:
    """Returns a fixed non-zero cost for any invocation."""

    def compute_cost(self, context: InvocationContext) -> CostRecord:
        return CostRecord(
            mcp_server_id=context.mcp_server_id,
            tool_name=context.tool_name,
            duration_ms=context.duration_ms,
            cost_cents=200,
        )


def _cost_line() -> str:
    return 'mcp_hangar_cost_cents_total{cost_model="duration",mcp_server="srv-cost-iso",tool="get-cost-iso"}'


def test_cost_handler_emits_metric() -> None:
    handler = CostAttributionEventHandler(cost_attributor=_FixedAttributor())
    event = ToolInvocationCompleted(
        mcp_server_id="srv-cost-iso",
        tool_name="get-cost-iso",
        correlation_id="c1",
        duration_ms=12.0,
        result_size_bytes=0,
    )
    handler.handle(event)

    out = m.get_metrics()
    assert _cost_line() in out
    assert 'mcp_hangar_cost_attributions_total{mcp_server="srv-cost-iso",tool="get-cost-iso"}' in out
