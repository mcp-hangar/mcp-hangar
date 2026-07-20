"""Cost attribution event handler.

Subscribes to ToolInvocationCompleted events, computes cost using
ICostAttributor, and emits CostReportGenerated domain events.
"""

from ...domain.contracts.cost import ICostAttributor, InvocationContext, NullCostAttributor
from ...domain.events import CostReportGenerated, ToolInvocationCompleted
from ...logging_config import get_logger

logger = get_logger(__name__)


class CostAttributionEventHandler:
    """Computes cost on every successful tool invocation and publishes metrics."""

    def __init__(
        self,
        cost_attributor: ICostAttributor | None = None,
        event_bus: object | None = None,
    ) -> None:
        self._attributor = cost_attributor or NullCostAttributor()
        self._event_bus = event_bus

    def handle(self, event: object) -> None:
        if not isinstance(event, ToolInvocationCompleted):
            return

        context = InvocationContext(
            mcp_server_id=event.mcp_server_id,
            tool_name=event.tool_name,
            duration_ms=event.duration_ms,
            correlation_id=event.correlation_id,
        )

        cost_record = self._attributor.compute_cost(context)
        if cost_record.cost_cents == 0:
            return

        from ...metrics import record_cost

        record_cost(
            mcp_server=cost_record.mcp_server_id,
            tool=cost_record.tool_name,
            cost_cents=cost_record.cost_cents,
            cost_model=str(cost_record.cost_model),
        )

        logger.debug(
            "cost_attributed",
            mcp_server_id=cost_record.mcp_server_id,
            tool_name=cost_record.tool_name,
            cost_cents=cost_record.cost_cents,
            cost_model=str(cost_record.cost_model),
        )

        if self._event_bus is not None and hasattr(self._event_bus, "publish"):
            cost_event = CostReportGenerated(
                tenant_id=cost_record.tenant_id,
                period_start="",
                period_end="",
                total_cost=str(cost_record.cost_cents / 100.0),
                currency=cost_record.currency,
            )
            self._event_bus.publish([cost_event])
