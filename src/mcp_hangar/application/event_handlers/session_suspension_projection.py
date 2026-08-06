"""Applying a suspension on every replica, not only the one that decided it.

The registry is a set in one process. A session suspended by a detection rule on
replica A was refused by A and served by B and C, so the block was avoided by
retrying the request -- an enforcement decision that any caller could walk past
without knowing it existed.

This is the projection that closes it: the decision travels as an event, and
every replica applies it to its own registry. The one that decided applies it
through its own local publish; the others through the tail (#801).

**A projection, not an effect** (#799). An effect runs only on the instance that
produced the event, which is exactly what was wrong before.

The propagation window is one tail interval, and it is the honest cost of not
reading the database on the path of every tool invocation. A suspension is a
response to behaviour that has already happened; two more seconds of it is a
worse outcome than adding a database round trip to every call in the system,
which is the alternative that was weighed.
"""

from __future__ import annotations

from ...domain.contracts.session_suspension import ISessionSuspensionRegistry
from ...domain.events import DomainEvent, SessionSuspended, SessionUnsuspended
from ...logging_config import get_logger

logger = get_logger(__name__)


class SessionSuspensionProjection:
    """Keeps this replica's suspended-session registry in step with the fleet."""

    def __init__(self, registry: ISessionSuspensionRegistry) -> None:
        """Bind to the registry this replica serves from.

        Args:
            registry: The same instance the request path checks and the HTTP
                routes report on. A second instance would mean a suspension
                that is recorded and never enforced.
        """
        self._registry = registry

    def handle(self, event: DomainEvent) -> None:
        """Apply a suspension decision to the local registry.

        Idempotent, as a projection must be (ADR-018): suspending an already
        suspended session refreshes it, and lifting an unknown one is not an
        error. That matters because the tail is at-least-once.
        """
        if isinstance(event, SessionSuspended):
            self._registry.suspend(event.session_id)
            logger.info(
                "session_suspension_applied",
                session_id=event.session_id,
                reason=event.reason,
                source=event.source,
                produced_by=event.produced_by,
            )
        elif isinstance(event, SessionUnsuspended):
            self._registry.unsuspend(event.session_id)
            logger.info(
                "session_suspension_lifted",
                session_id=event.session_id,
                reason=event.reason,
                source=event.source,
                produced_by=event.produced_by,
            )
