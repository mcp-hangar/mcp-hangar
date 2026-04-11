"""Bootstrap wiring for the approval gate module.

Called from src/mcp_hangar/server/bootstrap/enterprise.py
to initialize the approval gate service and wire it into
the application context.
"""

from typing import Any

from mcp_hangar.logging_config import get_logger

from .delivery.dashboard import DashboardApprovalDelivery
from .delivery.noop import NoOpApprovalDelivery
from .hold_registry import ApprovalHoldRegistry
from .persistence.sqlite_approval_repository import SqliteApprovalRepository
from .service import ApprovalGateService

logger = get_logger(__name__)


def bootstrap_approvals(
    database: Any,
    event_bus: Any,
    config: dict | None = None,
) -> ApprovalGateService:
    """Wire and return an ApprovalGateService instance.

    Args:
        database: Database instance for persistence.
        event_bus: Event bus for domain event publishing.
        config: Optional config dict with enterprise.approvals settings.

    Returns:
        Configured ApprovalGateService.
    """
    repository = SqliteApprovalRepository(database)
    hold_registry = ApprovalHoldRegistry()
    delivery = _build_delivery(config)

    service = ApprovalGateService(
        repository=repository,
        hold_registry=hold_registry,
        event_bus=event_bus,
        delivery=delivery,
    )

    logger.info(
        "approval_gate_bootstrapped",
        delivery=type(delivery).__name__,
    )

    return service


def _build_delivery(config: dict | None) -> Any:
    """Select delivery implementation based on config."""
    if config is None:
        return NoOpApprovalDelivery()

    approvals_config = config.get("enterprise", {}).get("approvals", {})
    channel = approvals_config.get("channel", "dashboard")

    if channel == "slack":
        try:
            from .delivery.slack import SlackApprovalDelivery

            slack_config = approvals_config.get("slack", {})
            return SlackApprovalDelivery(
                webhook_url=slack_config.get("webhook_url", ""),
                signing_secret=slack_config.get("signing_secret", ""),
            )
        except ImportError:
            logger.warning("slack_delivery_not_available")
            return NoOpApprovalDelivery()

    if channel == "dashboard":
        return DashboardApprovalDelivery()

    return NoOpApprovalDelivery()
