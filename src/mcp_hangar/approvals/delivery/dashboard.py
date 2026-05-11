"""Dashboard WebSocket push delivery for approval notifications."""

from mcp_hangar.logging_config import get_logger

from ..models import ApprovalRequest

logger = get_logger(__name__)


class DashboardApprovalDelivery:
    """Pushes approval notifications to connected dashboard clients via WebSocket.

    Placeholder -- actual WebSocket integration wired via event bus.
    """

    async def send(self, request: ApprovalRequest) -> None:
        logger.info(
            "dashboard_approval_delivery",
            approval_id=request.approval_id,
            tool=request.tool_name,
            provider=request.provider_id,
        )
