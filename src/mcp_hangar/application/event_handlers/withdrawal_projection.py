"""Applying a withdrawal on every replica, not only the one that took the POST.

`POST /admin/tools/{server}/{name}/withdraw` wrote a dict in one process. On a
fleet of N the withdrawn tool stayed listed and callable on the other N-1, so an
agent reached it by retrying until the load balancer sent it elsewhere -- and a
rolling restart lifted the withdrawal on the last replica too, while the
response had said `{"withdrawn": true}` and the REST reference said the
withdrawal persists (#1165).

Same shape as `SessionSuspensionProjection` (#801) and the L7 policy projection
(#991), for the same reason: an enforcement decision that reaches one replica is
a control that any caller can walk past without knowing it exists.

**A projection, not an effect.** An effect runs only on the instance that
produced the event, which is exactly what was wrong. It publishes nothing: an
event raised while applying a tailed one is echoed by every replica in turn.

**The event is the record here**, unlike the fleet projection next door where
the event is a notification and the row is the content. A withdrawal is four
small fields -- server, name, kind, tenant -- and all four are on the event, so
there is no row to read back and no window in which the two disagree. That is
also what makes the startup replay in `bootstrap.withdrawals` possible.

Idempotent, as the at-least-once tail requires: withdrawing an already withdrawn
name adds a tenant that is already in the set, and restoring one that is not
withdrawn returns without touching anything.
"""

from __future__ import annotations

from ...domain.events import DomainEvent, ToolRestored, ToolWithdrawn
from ...logging_config import get_logger
from ..read_models.tool_projection import ToolProjectionRegistry

logger = get_logger(__name__)


class WithdrawalProjection:
    """Keeps this replica's runtime withdrawal overlay in step with the fleet."""

    def __init__(self, registry: ToolProjectionRegistry) -> None:
        """Bind to the registry this replica enforces from.

        Args:
            registry: The same instance the call path and the listing consult.
                A second instance would be a withdrawal that is recorded and
                never enforced.
        """
        self._registry = registry

    def handle(self, event: DomainEvent) -> None:
        """Apply a withdrawal decision to the local runtime overlay."""
        if isinstance(event, ToolWithdrawn):
            self._registry.withdraw(event.mcp_server, event.tool, tenant_id=event.tenant_id, kind=event.kind)
            logger.info(
                "withdrawal_applied",
                mcp_server=event.mcp_server,
                tool=event.tool,
                kind=event.kind,
                tenant_id=event.tenant_id,
                produced_by=event.produced_by,
            )
        elif isinstance(event, ToolRestored):
            self._registry.restore(event.mcp_server, event.tool, tenant_id=event.tenant_id, kind=event.kind)
            logger.info(
                "withdrawal_lifted",
                mcp_server=event.mcp_server,
                tool=event.tool,
                kind=event.kind,
                tenant_id=event.tenant_id,
                produced_by=event.produced_by,
            )
