"""The built-in channel: the approval already travels on the event stream.

This class used to be ``DashboardApprovalDelivery``, named after a management
UI that shipped with the Hangar Cloud tier and was archived with it. Nothing
renders that UI any more, and nothing in this repo ever pushed to it -- the
docstring said "actual WebSocket integration wired via event bus" and no such
wiring existed. A channel named after a product that will not exist, whose
``send`` writes a log line, is the misleading half of #914.

What actually notifies a human is upstream of delivery.
:meth:`~mcp_hangar.approvals.service.ApprovalGateService.check` publishes
``ToolApprovalRequested`` before it calls ``send`` and before it starts waiting,
and ``/api/ws/events`` subscribes to every domain event
(:mod:`mcp_hangar.server.api.ws.events`), so any client holding ``audit:read``
receives the approval -- id, tool, channel label and expiry -- in real time,
whatever delivery is configured. That is the notification path this deployment
has out of the box, and it is a pull-free push.

So ``send`` here is deliberately a log line and nothing else: the work is
already done, and duplicating it into a second socket would only add a second
thing to fail. The name now says which surface carries it, so an operator
reading ``channel: event_stream`` can go and look at that surface.

An adapter that must reach somewhere the event stream does not go -- Slack, a
pager, a ticket queue -- is installed under the
``mcp_hangar.approvals.delivery`` entry-point group. See ADR-016.
"""

from mcp_hangar.logging_config import get_logger

from ..models import ApprovalRequest

logger = get_logger(__name__)


class EventStreamApprovalDelivery:
    """Records the hand-off; the push itself rides the domain event stream."""

    async def send(self, request: ApprovalRequest) -> None:
        logger.info(
            "event_stream_approval_delivery",
            approval_id=request.approval_id,
            tool=request.tool_name,
            provider=request.provider_id,
            channel=request.channel,
        )
