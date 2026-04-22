"""Group Rebalance Saga - automatically rebalances groups based on events.

This saga listens for mcp_server health events and updates group member
rotation status. The actual logic is delegated to McpServerGroup methods.

Note: Most of the group health management is already handled by McpServerGroup
through report_success() and report_failure() calls. This saga primarily
serves as an event-driven bridge for external events (like health checks)
that may not flow through the standard invoke path.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ...domain.events import (
    DomainEvent,
    HealthCheckFailed,
    HealthCheckPassed,
    McpServerDegraded,
    McpServerStarted,
    McpServerStopped,
)
from ...application.ports.saga import EventTriggeredSaga
from ...logging_config import get_logger
from ..commands import Command

if TYPE_CHECKING:
    from ...domain.model.mcp_server_group import McpServerGroup

logger = get_logger(__name__)


class GroupRebalanceSaga(EventTriggeredSaga):
    """
    Saga that observes mcp_server events for group members.

    This saga tracks which mcp_servers belong to which groups and logs
    relevant events. The actual rotation management is handled by
    McpServerGroup through its report_success/report_failure methods.

    The saga can optionally execute direct actions on groups if provided
    with a groups reference.
    """

    def __init__(
        self,
        group_lookup: Callable[[str], str | None] | None = None,
        groups: dict[str, McpServerGroup] | None = None,
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
            McpServerStarted,
            McpServerStopped,
            McpServerDegraded,
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

    def _get_group(self, group_id: str) -> McpServerGroup | None:
        """Get group instance if available."""
        if self._groups:
            return self._groups.get(group_id)
        return None

    def handle(self, event: DomainEvent) -> list[Command]:
        """
        Handle mcp_server events that affect group membership.

        Returns empty list as we apply changes directly to groups
        rather than emitting commands.
        """
        mcp_server_id = getattr(event, "mcp_server_id", None)
        if not mcp_server_id:
            return []

        group_id = self._get_group_id(mcp_server_id)
        if not group_id:
            return []

        group = self._get_group(group_id)

        if isinstance(event, McpServerStarted):
            logger.info(f"Member {mcp_server_id} started in group {group_id}")
            if group:
                group.report_success(mcp_server_id)

        elif isinstance(event, McpServerStopped | McpServerDegraded):
            reason = getattr(event, "reason", "unknown")
            logger.info(f"Member {mcp_server_id} unavailable in group {group_id}: {reason}")
            if group:
                group.report_failure(mcp_server_id)

        elif isinstance(event, HealthCheckPassed):
            logger.debug(f"Health check passed for {mcp_server_id} in group {group_id}")
            if group:
                group.report_success(mcp_server_id)

        elif isinstance(event, HealthCheckFailed):
            logger.debug(f"Health check failed for {mcp_server_id} in group {group_id}")
            if group:
                group.report_failure(mcp_server_id)

        return []

    def to_dict(self) -> dict[str, Any]:
        """Return empty dict -- this saga has no meaningful state to persist."""
        return {}

    def from_dict(self, data: dict[str, Any]) -> None:
        """No-op -- state is transient, rebuilt from group objects at init."""
        pass
