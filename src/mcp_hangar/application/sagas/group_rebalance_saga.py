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
    DEGRADED_BY_HEALTH_CHECKS,
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
    Saga that feeds mcp_server events for group members into their groups.

    With a groups mapping, a member's groups are read from the groups
    themselves, on every event, and the event is reported to each of them
    through report_success/report_failure. Without one, the saga can only
    resolve a group id and log.
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
                          the group_id it belongs to, or None. Consulted only
                          without a groups mapping.
            groups: The live groups mapping -- the one members are added to,
                    not a copy -- for applying changes.
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
        """Register a member-to-group mapping, consulted only without a groups mapping."""
        self._member_to_group[member_id] = group_id

    def unregister_member(self, member_id: str) -> None:
        """Unregister a member from the mapping."""
        self._member_to_group.pop(member_id, None)

    def _get_group_id(self, member_id: str) -> str | None:
        """Get the group ID for a member from the registered mapping or the lookup."""
        group_id = self._member_to_group.get(member_id)
        if group_id:
            return group_id
        if self._group_lookup:
            return self._group_lookup(member_id)
        return None

    def _groups_of(self, member_id: str) -> list[tuple[str, McpServerGroup | None]]:
        """Every group the member is in, with the group object when there is one.

        Read from the groups themselves when the saga has them. A side table of
        members has to be filled by every path that adds one, and the served
        path filled none: `bootstrap()` loads the servers before it creates this
        saga, so `_load_group_members` never found a saga to register with, and
        every `HealthCheckPassed` stopped here (#1355). The groups are also the
        only place that knows a member sits in two groups, or has moved.
        """
        if self._groups is not None:
            return [
                (group_id, group)
                for group_id, group in list(self._groups.items())
                if group.get_member(member_id) is not None
            ]
        group_id = self._get_group_id(member_id)
        return [(group_id, None)] if group_id else []

    def handle(self, event: DomainEvent) -> list[Command]:
        """
        Handle mcp_server events that affect group membership.

        Returns empty list as we apply changes directly to groups
        rather than emitting commands.
        """
        mcp_server_id = getattr(event, "mcp_server_id", None)
        if not mcp_server_id:
            return []

        for group_id, group in self._groups_of(mcp_server_id):
            self._apply(event, mcp_server_id, group_id, group)

        return []

    def _apply(self, event: DomainEvent, mcp_server_id: str, group_id: str, group: McpServerGroup | None) -> None:
        """Report one event about a member to one of its groups."""
        if isinstance(event, McpServerStarted):
            logger.info(f"Member {mcp_server_id} started in group {group_id}")
            if group:
                group.report_success(mcp_server_id)

        elif isinstance(event, McpServerStopped):
            # Not a failure. A stop carries one of two reasons, and the gateway
            # or an operator chose both: "idle" is the GC reaping an unused
            # server, "shutdown" is every explicit stop (hangar_stop, reload,
            # unload, delete, a group's stop_all, process exit). A crashed
            # process emits no stop at all. Counted as a failure, one idle reap
            # could take a cold member out of rotation, where nothing selects,
            # health-checks or starts it again.
            logger.info(f"Member {mcp_server_id} stopped in group {group_id}: {event.reason}")

        elif isinstance(event, McpServerDegraded):
            if event.reason == DEGRADED_BY_HEALTH_CHECKS:
                # The check that degraded the server has already been reported
                # as its own HealthCheckFailed; counting this too would count
                # one failure twice.
                logger.info(f"Member {mcp_server_id} degraded by health checks in group {group_id}")
            else:
                logger.info(f"Member {mcp_server_id} degraded in group {group_id}: {event.reason}")
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

    def to_dict(self) -> dict[str, Any]:
        """Return empty dict -- this saga has no meaningful state to persist."""
        return {}

    def from_dict(self, data: dict[str, Any]) -> None:
        """No-op -- state is transient, rebuilt from group objects at init."""
        pass
