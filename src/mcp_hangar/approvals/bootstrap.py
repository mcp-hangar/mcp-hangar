"""Bootstrap wiring for the approval gate module.

Called from src/mcp_hangar/server/bootstrap/components.py
to initialize the approval gate service and wire it into
the application context.
"""

from typing import Any

from mcp_hangar.logging_config import get_logger

from mcp_hangar.metrics import APPROVAL_DELIVERIES_TOTAL

from .delivery.event_stream import EventStreamApprovalDelivery
from .delivery.noop import NoOpApprovalDelivery
from .hold_registry import ApprovalHoldRegistry
from .models import ApprovalRequest
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
    delivery = ApprovalDeliveryRouter(config)

    service = ApprovalGateService(
        repository=repository,
        hold_registry=hold_registry,
        event_bus=event_bus,
        delivery=delivery,
    )

    logger.info(
        "approval_gate_bootstrapped",
        delivery=type(delivery).__name__,
        default_channel=delivery.default_channel,
        notifies=channel_reaches_a_human(delivery.default_channel),
    )

    return service


#: Entry-point group third-party approval channels register under. An entry
#: point resolves to a callable taking the channel's config dict and returning
#: something satisfying the ``ApprovalDelivery`` protocol.
DELIVERY_ENTRY_POINT_GROUP = "mcp_hangar.approvals.delivery"

#: Channels core itself provides. Neither reaches outside the process.
_BUILTIN_DELIVERIES: dict[str, Any] = {
    "event_stream": lambda _config: EventStreamApprovalDelivery(),
    "noop": lambda _config: NoOpApprovalDelivery(),
}

#: The channel selected when the config names none.
DEFAULT_CHANNEL = "event_stream"

#: Old channel names that still resolve, and what they resolve to. ``dashboard``
#: was named after the Hangar Cloud management UI, which was archived with that
#: tier and will not ship. The name outlived the product and described a push
#: this repo never performed; the push that does happen rides the domain event
#: stream, which is what the channel is now called. Kept resolving rather than
#: rejected: an operator who wrote ``channel: dashboard`` gets the same delivery
#: they had, plus one line telling them the name moved.
_CHANNEL_ALIASES: dict[str, str] = {
    "dashboard": "event_stream",
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


#: Channels that resolve, but reach nothing outside this process. A gate whose
#: effective delivery is one of these is armed and unmanned: the hold works, the
#: timeout works, and nobody is told (#914). ``event_stream`` is deliberately not
#: here -- its notification rides the domain event stream that ``/api/ws/events``
#: serves, so a connected client does hear about the hold.
_SILENT_CHANNELS = frozenset({"noop"})


def resolve_channel(configured: str) -> str:
    """Return the channel *configured* means today, following any rename."""
    return _CHANNEL_ALIASES.get(configured, configured)


def configured_channel(config: dict | None) -> str:
    """The globally configured default channel, after alias resolution."""
    if config is None:
        return "noop"
    return resolve_channel(config.get("approvals", {}).get("channel", DEFAULT_CHANNEL))


def channel_reaches_a_human(channel: str) -> bool:
    """Whether *channel* can notify anyone outside this process.

    A builtin answers for itself. Anything else has to be claimed by an installed
    entry point -- an unclaimed name degrades to ``noop`` at build time, so it
    reaches nobody however it was spelled in the config.
    """
    channel = resolve_channel(channel)
    if channel in _SILENT_CHANNELS:
        return False
    if channel in _BUILTIN_DELIVERIES:
        return True
    return _load_delivery_entry_point(channel) is not None


def _build_delivery(config: dict | None) -> Any:
    """Select the approval delivery channel.

    Core ships ``event_stream`` and ``noop`` and knows no vendors. Anything else
    is looked up in the ``mcp_hangar.approvals.delivery`` entry-point group, so a
    vendor adapter is installed rather than imported from here.

    This used to hardcode ``"slack"`` and import ``.delivery.slack``, which put a
    vendor's Block Kit payloads and signing scheme in the core tree. The outbound
    side was already behind the ``ApprovalDelivery`` protocol; the coupling was
    the branch above it. See ADR-016 and A-2919 WS-4.

    An unknown channel degrades to ``noop`` with a warning rather than failing
    startup: approvals then queue undelivered and remain resolvable through the
    REST API, which is recoverable. Refusing to boot because a notification
    channel is missing is not. The startup reachability check says so out loud
    instead -- see ``server/bootstrap/reachability.py`` (#914).
    """
    if config is None:
        return NoOpApprovalDelivery()

    approvals_config = config.get("approvals", {})
    configured = approvals_config.get("channel", DEFAULT_CHANNEL)
    channel = resolve_channel(configured)
    if channel != configured:
        logger.warning(
            "approval_delivery_channel_renamed",
            channel=configured,
            resolved_to=channel,
        )

    return _build_channel(channel, approvals_config, spelled=configured)


def _build_channel(channel: str, approvals_config: dict, *, spelled: str | None = None) -> Any:
    """Build one channel's delivery, degrading to ``noop`` rather than raising."""
    #: An aliased channel keeps reading its old config block, so a rename never
    #: silently drops the settings underneath it.
    channel_config = approvals_config.get(channel) or approvals_config.get(spelled or channel) or {}

    builtin = _BUILTIN_DELIVERIES.get(channel)
    if builtin is not None:
        return builtin(channel_config)

    factory = _load_delivery_entry_point(channel)
    if factory is not None:
        try:
            return factory(channel_config)
        except Exception:  # noqa: BLE001 -- same reasoning as above
            logger.warning("approval_delivery_construction_failed", channel=channel, exc_info=True)
            return NoOpApprovalDelivery()

    logger.warning(
        "approval_delivery_channel_unknown",
        channel=spelled or channel,
        known=sorted(_BUILTIN_DELIVERIES),
        group=DELIVERY_ENTRY_POINT_GROUP,
    )
    return NoOpApprovalDelivery()


class ApprovalDeliveryRouter:
    """Sends each approval through the channel its policy asked for.

    ``ToolAccessPolicy.approval_channel`` and ``MCPServerConfig.approval_channel``
    were documented as the delivery channel for approval notifications, merged
    with care across policy narrowing -- ``min`` timeout, channel taken from the
    narrower policy that owns the ``approval_list`` -- and then dispatched
    nowhere. One global delivery handled every approval whichever policy raised
    it, so a deployment could set ``approval_channel: slack`` on one server and
    something else on another and get one channel, silently, with no error
    (#914). Declared configuration that cannot take effect is worse than either
    routing on it or removing it.

    This routes on it. It satisfies the ``ApprovalDelivery`` protocol itself, so
    the gate service calls ``send`` exactly as before and knows nothing about
    channels.

    Channels are built on first use rather than enumerated at startup: a policy
    can arrive after boot, from a hot config reload or over REST, and a channel
    that only exists then would otherwise never be routable.
    """

    def __init__(self, config: dict | None) -> None:
        self._approvals_config = (config or {}).get("approvals", {}) or {}
        self._default_channel = configured_channel(config)
        self._deliveries: dict[str, Any] = {self._default_channel: _build_delivery(config)}

    @property
    def default_channel(self) -> str:
        """The channel an approval uses when its policy names none."""
        return self._default_channel

    def _delivery_for(self, channel: str | None) -> Any:
        if not channel:
            return self._deliveries[self._default_channel]

        resolved = resolve_channel(channel)
        existing = self._deliveries.get(resolved)
        if existing is not None:
            return existing

        built = _build_channel(resolved, self._approvals_config, spelled=channel)
        self._deliveries[resolved] = built
        logger.info("approval_delivery_channel_routed", channel=resolved, delivery=type(built).__name__)
        return built

    async def send(self, request: ApprovalRequest) -> None:
        """Deliver *request* through the channel it names."""
        channel = resolve_channel(request.channel) if request.channel else self._default_channel
        delivery = self._delivery_for(request.channel)

        if isinstance(delivery, NoOpApprovalDelivery):
            APPROVAL_DELIVERIES_TOTAL.inc(channel=channel, outcome="not_notified")
        else:
            APPROVAL_DELIVERIES_TOTAL.inc(channel=channel, outcome="sent")

        await delivery.send(request)
