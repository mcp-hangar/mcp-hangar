"""No-op approval delivery for development and testing.

Logs the request but does NOT auto-resolve. Test harnesses call
hold_registry.resolve() directly.
"""

from mcp_hangar.logging_config import get_logger

from ..models import ApprovalRequest

logger = get_logger(__name__)


class NoOpApprovalDelivery:
    """Development/test delivery that logs and does nothing else."""

    async def send(self, request: ApprovalRequest) -> None:
        logger.info(
            "noop_approval_delivery",
            approval_id=request.approval_id,
            tool=request.tool_name,
            provider=request.provider_id,
            channel=request.channel,
        )
