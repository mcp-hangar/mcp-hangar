"""Cost attribution event handler.

Subscribes to ToolInvocationCompleted events, computes cost using
ICostAttributor, and emits CostReportGenerated domain events.
"""

from ...domain.contracts.cost import ICostAttributor, InvocationContext, NullCostAttributor
from ...domain.contracts.event_bus import IEventBus
from ...domain.events import CostReportGenerated, ToolInvocationCompleted
from ...logging_config import get_logger

logger = get_logger(__name__)


class CostAttributionEventHandler:
    """Computes cost on every successful tool invocation and publishes the result."""

    def __init__(
        self,
        cost_attributor: ICostAttributor | None = None,
        event_bus: IEventBus | None = None,
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

        # The counter itself lives in MetricsEventHandler with every other one.
        # This handler computes the cost and publishes it; recording it is the
        # metrics adapter's job, reached over the bus like everything else.
        logger.debug(
            "cost_attributed",
            mcp_server_id=cost_record.mcp_server_id,
            tool_name=cost_record.tool_name,
            cost_cents=cost_record.cost_cents,
            cost_model=str(cost_record.cost_model),
        )

        if self._event_bus is not None:
            cost_event = CostReportGenerated(
                tenant_id=cost_record.tenant_id,
                period_start="",
                period_end="",
                total_cost=str(cost_record.cost_cents / 100.0),
                currency=cost_record.currency,
                mcp_server_id=cost_record.mcp_server_id,
                tool_name=cost_record.tool_name,
                cost_model=str(cost_record.cost_model),
                cost_cents=cost_record.cost_cents,
            )
            # NOT publish([cost_event]): the bus takes one event, and a list reached
            # every subscribe-to-all handler as a `list` object while anything
            # subscribed to CostReportGenerated got nothing at all.
            self._event_bus.publish(cost_event)
