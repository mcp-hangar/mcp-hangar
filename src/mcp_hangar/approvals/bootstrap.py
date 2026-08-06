"""Bootstrap wiring for the approval gate module.

Called from src/mcp_hangar/server/bootstrap/components.py
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
    repository: Any = None,
) -> ApprovalGateService:
    """Wire and return an ApprovalGateService instance.

    Args:
        database: Database instance for persistence. Ignored when `repository`
            is supplied.
        event_bus: Event bus for domain event publishing.
        config: Optional config dict with mcp_hangar.approvals settings.
        repository: The approval repository from the selected storage backend.
            None means build the SQLite one from `database`, which is the
            compatibility path for a deployment that selected no backend.

    Returns:
        Configured ApprovalGateService.
    """
    if repository is None:
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


#: Entry-point group third-party approval channels register under. An entry
#: point resolves to a callable taking the channel's config dict and returning
#: something satisfying the ``ApprovalDelivery`` protocol.
DELIVERY_ENTRY_POINT_GROUP = "mcp_hangar.approvals.delivery"

#: Channels core itself provides. Neither reaches outside the process.
_BUILTIN_DELIVERIES: dict[str, Any] = {
    "dashboard": lambda _config: DashboardApprovalDelivery(),
    "noop": lambda _config: NoOpApprovalDelivery(),
}


def _load_delivery_entry_point(channel: str) -> Any | None:
    """Resolve *channel* from installed packages, or None if nothing claims it."""
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover - importlib.metadata is stdlib on 3.11+
        logger.debug("importlib_metadata_unavailable", channel=channel)
        return None

    for entry_point in entry_points(group=DELIVERY_ENTRY_POINT_GROUP):
        if entry_point.name != channel:
            continue
        try:
            return entry_point.load()
        except Exception:  # noqa: BLE001 -- a broken plugin must not take the gateway down
            logger.warning(
                "approval_delivery_entry_point_failed",
                channel=channel,
                entry_point=entry_point.value,
                exc_info=True,
            )
            return None
    return None


def _build_delivery(config: dict | None) -> Any:
    """Select the approval delivery channel.

    Core ships ``dashboard`` and ``noop`` and knows no vendors. Anything else is
    looked up in the ``mcp_hangar.approvals.delivery`` entry-point group, so a
    vendor adapter is installed rather than imported from here.

    This used to hardcode ``"slack"`` and import ``.delivery.slack``, which put a
    vendor's Block Kit payloads and signing scheme in the core tree. The outbound
    side was already behind the ``ApprovalDelivery`` protocol; the coupling was
    the branch above it. See ADR-016 and A-2919 WS-4.

    An unknown channel degrades to ``noop`` with a warning rather than failing
    startup: approvals then queue undelivered and remain resolvable through the
    REST API, which is recoverable. Refusing to boot because a notification
    channel is missing is not.
    """
    if config is None:
        return NoOpApprovalDelivery()

    approvals_config = config.get("approvals", {})
    channel = approvals_config.get("channel", "dashboard")

    builtin = _BUILTIN_DELIVERIES.get(channel)
    if builtin is not None:
        return builtin(approvals_config.get(channel, {}))

    factory = _load_delivery_entry_point(channel)
    if factory is not None:
        try:
            return factory(approvals_config.get(channel, {}))
        except Exception:  # noqa: BLE001 -- same reasoning as above
            logger.warning("approval_delivery_construction_failed", channel=channel, exc_info=True)
            return NoOpApprovalDelivery()

    logger.warning(
        "approval_delivery_channel_unknown",
        channel=channel,
        known=sorted(_BUILTIN_DELIVERIES),
        group=DELIVERY_ENTRY_POINT_GROUP,
    )
    return NoOpApprovalDelivery()
