"""Group Rebalance Saga - automatically rebalances groups based on events.

This saga listens for provider health events and updates group member
rotation status. The actual logic is delegated to ProviderGroup methods.

Note: Most of the group health management is already handled by ProviderGroup
through report_success() and report_failure() calls. This saga primarily
serves as an event-driven bridge for external events (like health checks)
that may not flow through the standard invoke path.
"""

from collections.abc import Callable
from typing import Optional, TYPE_CHECKING

from ...domain.events import (
    DomainEvent,
    HealthCheckFailed,
    HealthCheckPassed,
    ProviderDegraded,
    ProviderStarted,
    ProviderStopped,
)
from ...infrastructure.saga_manager import EventTriggeredSaga
from ...logging_config import get_logger
from ..commands import Command

if TYPE_CHECKING:
    from ...domain.model.provider_group import ProviderGroup

logger = get_logger(__name__)


class GroupRebalanceSaga(EventTriggeredSaga):
    """
    Saga that observes provider events for group members.

    This saga tracks which providers belong to which groups and logs
    relevant events. The actual rotation management is handled by
    ProviderGroup through its report_success/report_failure methods.

    The saga can optionally execute direct actions on groups if provided
    with a groups reference.
    """

    def __init__(
        self,
        group_lookup: Callable[[str], str | None] | None = None,
        groups: dict[str, "ProviderGroup"] | None = None,
    ):
        """
        Initialize the saga.

        Args:
            group_lookup: Function that takes a member_id and returns
                          the group_id it belongs to, or None.
            groups: Direct reference to groups dict for applying changes.
        """
        super().__init__()
        self._group_lookup = group_lookup
        self._groups = groups
        self._member_to_group: dict[str, str] = {}

    @property
    def saga_type(self) -> str:
        return "group_rebalance"

    @property
    def handled_events(self) -> list[type[DomainEvent]]:
        return [
            ProviderStarted,
            ProviderStopped,
            ProviderDegraded,
            HealthCheckPassed,
            HealthCheckFailed,
        ]

    def register_member(self, member_id: str, group_id: str) -> None:
        """Register a member-to-group mapping."""
        self._member_to_group[member_id] = group_id

    def unregister_member(self, member_id: str) -> None:
        """Unregister a member from the mapping."""
        self._member_to_group.pop(member_id, None)

    def _get_group_id(self, member_id: str) -> str | None:
        """Get the group ID for a member."""
        group_id = self._member_to_group.get(member_id)
        if group_id:
            return group_id
        if self._group_lookup:
            return self._group_lookup(member_id)
        return None

    def _get_group(self, group_id: str) -> Optional["ProviderGroup"]:
        """Get group instance if available."""
        if self._groups:
            return self._groups.get(group_id)
        return None

    def handle(self, event: DomainEvent) -> list[Command]:
        """
        Handle provider events that affect group membership.

        Returns empty list as we apply changes directly to groups
        rather than emitting commands.
        """
        provider_id = getattr(event, "provider_id", None)
        if not provider_id:
            return []

        group_id = self._get_group_id(provider_id)
        if not group_id:
            return []

        group = self._get_group(group_id)

        if isinstance(event, ProviderStarted):
            logger.info(f"Member {provider_id} started in group {group_id}")
            if group:
                group.report_success(provider_id)

        elif isinstance(event, ProviderStopped | ProviderDegraded):
            reason = getattr(event, "reason", "unknown")
            logger.info(f"Member {provider_id} unavailable in group {group_id}: {reason}")
            if group:
                group.report_failure(provider_id)

        elif isinstance(event, HealthCheckPassed):
            logger.debug(f"Health check passed for {provider_id} in group {group_id}")
            if group:
                group.report_success(provider_id)

        elif isinstance(event, HealthCheckFailed):
            logger.debug(f"Health check failed for {provider_id} in group {group_id}")
            if group:
                group.report_failure(provider_id)

        return []
