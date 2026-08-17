"""A replica learns about servers it did not register.

The last piece of one fleet seen from three places. A server registered on
replica A -- by an operator, or by the discovery loop A happens to be running --
existed only in A's memory until A restarted, so B and C answered "no such
server" for it. Which replica knew about which server depended on where the load
balancer had sent each registration.

## The event is the notification; the row is the content

`McpServerRegistered` carries an id, a source and a mode. That is not enough to
rebuild a server, and enriching it with the whole configuration would mean
versioning the event every time a configuration field is added -- on a
persisted, replayable surface.

So the projection reads the configuration from the storage backend every replica
already shares. That works because of an ordering decision made earlier for a
different reason: registration writes the snapshot **before** it joins the fleet
and before it publishes (#794), so by the time this event exists, the row it
describes is committed. The event says *something changed and here is which
server*; the record says *what it is*.

## Why this is a projection

It keeps a local view. Every replica needs it, for every event, whoever produced
it -- that is the entire point. It publishes nothing: raising an event while
applying a tailed one would be echoed by every replica in turn, forever.

It is also idempotent, which the tail requires (at-least-once): a server already
in the fleet is left exactly as it is, because the local copy may be *running*
and the record describes configuration, not state.
"""

from __future__ import annotations

from typing import Any

from ...domain.contracts.persistence import IMcpServerConfigRepository
from ..ports.async_task import IBlockingAsyncRunner
from ...domain.events import DomainEvent, McpServerDeregistered, McpServerRegistered
from ...domain.events.enforcement import EgressPolicyCleared, EgressPolicySet
from ...domain.policies.egress_l7 import L7Policy
from ...domain.repository import IMcpServerRepository
from ...domain.services.fleet_snapshot import server_from_snapshot
from ...logging_config import get_logger

logger = get_logger(__name__)

#: A single row read. Long enough for a busy pool, short enough that a wedged
#: database does not hold the tail behind one event.
READ_TIMEOUT_S = 10.0


class FleetProjection:
    """Applies fleet membership changes decided elsewhere to the local fleet."""

    def __init__(
        self,
        repository: IMcpServerRepository,
        config_repository: IMcpServerConfigRepository,
        runner: IBlockingAsyncRunner,
    ) -> None:
        """Bind to the fleet this replica serves from and the shared record.

        Args:
            repository: The in-memory fleet the request path resolves against.
            config_repository: The shared configuration store -- the same one
                registration writes to, which is what makes the row available.
            runner: Runs the read and waits for it. Injected rather than
                constructed here: it is a thread and an event loop, which the
                application layer has no business knowing about -- and the
                import contract says so out loud.
        """
        self._repository = repository
        self._configs = config_repository
        self._runner = runner

    def handle(self, event: DomainEvent) -> None:
        """Apply a registration or a deregistration to the local fleet."""
        if isinstance(event, McpServerRegistered):
            self._register(event.mcp_server_id, event.produced_by)
        elif isinstance(event, McpServerDeregistered):
            self._deregister(event.mcp_server_id, event.produced_by)
        elif isinstance(event, (EgressPolicySet, EgressPolicyCleared)):
            self._apply_l7(event.mcp_server_id)

    def _apply_l7(self, mcp_server_id: str) -> None:
        """Apply a peer's L7 policy change to the local copy of the server.

        The event is an audit summary (counts and group names) -- deliberately
        not the rule set -- so the policy is read from the shared record the
        handler saved before publishing, same contract as registration (#991).
        Idempotent: setting the same policy twice is the same policy.
        """
        mcp_server = self._repository.get(mcp_server_id)
        if mcp_server is None:
            # Not in the local fleet: nothing to enforce here. If it registers
            # later, server_from_snapshot restores the policy with the row.
            return

        snapshot = self._read(mcp_server_id)
        if snapshot is None:
            # Fail closed the visible way: keep the local policy as it is and
            # say so. Applying None on an unreadable row would LIFT enforcement
            # on this replica because of a read hiccup.
            logger.warning(
                "fleet_projection_l7_row_unreadable",
                mcp_server_id=mcp_server_id,
                detail="peer changed the L7 policy but the stored row could not be read; local policy left unchanged",
            )
            return

        try:
            policy = L7Policy.from_dict(snapshot.l7_policy) if snapshot.l7_policy is not None else None
        except ValueError as error:
            logger.warning(
                "fleet_projection_l7_row_malformed",
                mcp_server_id=mcp_server_id,
                error=str(error),
            )
            return

        mcp_server.set_l7_policy(policy)
        logger.info(
            "fleet_projection_l7_applied",
            mcp_server_id=mcp_server_id,
            cleared=policy is None,
        )

    def _register(self, mcp_server_id: str, produced_by: str) -> None:
        if self._repository.exists(mcp_server_id):
            # Already here. Rebuilding it from the record would replace a server
            # that may be running with a COLD copy of its own configuration --
            # the projection would be undoing the thing it is projecting.
            return

        snapshot = self._read(mcp_server_id)
        if snapshot is None:
            # The row should exist: registration writes it before publishing.
            # Absent means either a deployment with no durable storage -- where
            # there are no peers to learn from either -- or a registration that
            # was rolled back after its event escaped. Neither is worth guessing
            # a configuration for.
            logger.warning(
                "fleet_projection_no_record",
                mcp_server_id=mcp_server_id,
                produced_by=produced_by,
                detail="registered elsewhere but no stored configuration; this replica cannot serve it",
            )
            return

        self._repository.add(mcp_server_id, server_from_snapshot(snapshot))
        logger.info("fleet_projection_registered", mcp_server_id=mcp_server_id, produced_by=produced_by)

    def _deregister(self, mcp_server_id: str, produced_by: str) -> None:
        if not self._repository.exists(mcp_server_id):
            return
        self._repository.remove(mcp_server_id)
        logger.info("fleet_projection_deregistered", mcp_server_id=mcp_server_id, produced_by=produced_by)

    def _read(self, mcp_server_id: str) -> Any:
        try:
            return self._runner.run(self._configs.get(mcp_server_id), READ_TIMEOUT_S)
        except Exception as error:  # noqa: BLE001 -- fault-barrier: one unreadable row must not stall the tail
            logger.warning("fleet_projection_read_failed", mcp_server_id=mcp_server_id, error=str(error))
            return None
