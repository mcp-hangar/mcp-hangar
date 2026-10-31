"""A tool access policy set on one replica applies on every replica (#2172).

`ToolAccessPolicySet` carries the two lists and not the approval fields, so as in
the fleet projection the event is the notification and the row is the content.
The handler stores the policy before it publishes, and each peer reads the row
back and applies it as the startup replay does. A group's policy is keyed by the
group's id, so a member a peer's change just added is governed by it as soon as
both have arrived, in either order.

A projection: every replica needs it, and it publishes nothing. Idempotent, as
the tail requires: applying one row twice is applying it once. This replica's
own events are skipped, because the handler applied the policy before publishing.
"""

from __future__ import annotations

from typing import Any

from mcp_hangar.domain.events import DomainEvent, ToolAccessPolicySet
from mcp_hangar.domain.events.producer import current_instance_id
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver, get_tool_access_resolver
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


def apply_stored_policy(resolver: ToolAccessResolver, scope: str, target_id: str, stored: ToolAccessPolicy) -> None:
    """Enforce a stored policy, keeping the approval gate of an older row that cannot carry one (#915)."""
    policy = stored
    if not stored.approval_list:
        in_force = resolver.get_configured_policy(scope, target_id)
        if in_force is not None and in_force.approval_list:
            policy = ToolAccessPolicy.with_access_lists(stored.allow_list, stored.deny_list, carrying_from=in_force)
            logger.info(
                "tap_replay_carried_approval_gate",
                scope=scope,
                target_id=target_id,
                reason="stored_row_predates_approval_columns",
            )
    if scope == "provider":
        resolver.set_mcp_server_policy(target_id, policy)
    elif scope == "group":
        resolver.set_group_policy(target_id, policy)
    elif scope == "member":
        parts = target_id.split(":", 1)
        if len(parts) == 2:
            resolver.set_member_policy(parts[0], parts[1], policy)
        else:
            resolver.set_member_policy(target_id, target_id, policy)


class ToolAccessPolicyProjection:
    """Applies a peer's stored tool access policy to this replica's resolver."""

    def __init__(self, tap_store: Any, instance_id: str | None = None) -> None:
        """Bind to the policy store the replicas share, as this replica or this process."""
        self._tap_store = tap_store
        self._instance_id = instance_id or current_instance_id()

    def handle(self, event: DomainEvent) -> None:
        """Read back the policy a peer stored and enforce it here."""
        if not isinstance(event, ToolAccessPolicySet) or event.produced_by == self._instance_id:
            return
        try:
            stored = self._tap_store.get_policy(event.scope, event.target_id)
        except Exception as error:  # noqa: BLE001 -- fault-barrier: one unreadable row must not stall the tail
            logger.warning(
                "tool_access_policy_row_unreadable",
                scope=event.scope,
                target_id=event.target_id,
                error=str(error),
                detail="a peer changed the policy but the row could not be read; the local policy is unchanged",
            )
            return
        if stored is None:
            # Cleared since. A clear changes what a restart replays, on each replica alike.
            return
        apply_stored_policy(get_tool_access_resolver(), event.scope, event.target_id, stored)
        logger.info("tool_access_policy_applied", scope=event.scope, target_id=event.target_id)
