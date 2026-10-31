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

## Group changes reach running replicas

A group change made over REST travels the same way (#2172). The handler stores
the row, then publishes, and each peer applies what the event names from the
rows, by the rules a restart uses (`GroupRules.restore`). A change on this replica
is already applied when its event is published, so its own events are skipped,
before the lock: a publisher may be anywhere.

The file wins for what it declares: a group this replica's file declares keeps
its configuration and the members it names, and leaves only with the file.

A row can name a server this replica does not hold yet, because the log gives no
order between two streams. That member joins when the server registers. Events
whose rows could not be read wait for the next event, as a key-pinned server in
`_unread` does.

Nothing here records a shared event on a group: `restore_member`, `adopt_config`
and `drop_member` exist for that. An event left on the aggregate would go out at
its next drain, under this replica's name, and every peer would answer it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from ...domain.contracts.persistence import (
    IMcpServerConfigRepository,
    McpServerConfigSnapshot,
    StoredGroups,
    peer_key_claim,
)
from ...domain.events import DomainEvent, McpServerDeregistered, McpServerRegistered, McpServerUpdated
from ...domain.events.enforcement import EgressPolicyCleared, EgressPolicySet
from ...domain.events.producer import current_instance_id
from ...domain.model import McpServer, McpServerGroup
from ...domain.model.mcp_server_group import (
    GroupCreated,
    GroupDeleted,
    GroupMemberAdded,
    GroupMemberRemoved,
    GroupUpdated,
)
from ...domain.policies.egress_l7 import L7Policy
from ...domain.repository import IMcpServerRepository
from ...domain.services.fleet_snapshot import server_from_snapshot
from ...domain.services.governance_overlays import swapping
from ...domain.value_objects.peer_identity import PeerIdentity, connection_declaration
from ...logging_config import get_logger
from ..group_membership import fill_vacant_member, vacate_member
from ..ports.async_task import IAsyncTaskSubmitter, IBlockingAsyncRunner

logger = get_logger(__name__)

#: A single row read. Long enough for a busy pool, short enough that a wedged
#: database does not hold the tail behind one event.
READ_TIMEOUT_S = 10.0

#: What a group's configuration changing looks like on the log.
GROUP_EVENTS = (GroupCreated, GroupUpdated, GroupDeleted, GroupMemberAdded, GroupMemberRemoved)


@dataclass(frozen=True)
class GroupRules:
    """How rows become groups here, which the server layer owns with the file's declarations."""

    #: Puts rows into the groups, as a restart does: the file's groups win, and
    #: nothing is recorded or started.
    restore: Callable[[StoredGroups], None]
    #: Removes a group a peer deleted.
    drop: Callable[[str], None]
    #: The members the file names for a group, or None when it declares no such group.
    declared_members: Callable[[str], frozenset[str] | None]


class FleetProjection:
    """Applies fleet membership changes decided elsewhere to the local fleet."""

    def __init__(
        self,
        repository: IMcpServerRepository,
        config_repository: IMcpServerConfigRepository,
        runner: IBlockingAsyncRunner,
        closer: IAsyncTaskSubmitter | None = None,
        declared_in_config: Callable[[str], bool] | None = None,
        groups: Mapping[str, McpServerGroup] | None = None,
        group_rules: GroupRules | None = None,
        instance_id: str | None = None,
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
            closer: Closes the client of a copy the projection retired, off the
                tail: the projection itself does no I/O beyond its reads. None
                closes nothing, which leaves the connection to the process.
            declared_in_config: Whether the configuration file declares a
                server. Such a server changes on each replica when it reloads
                the file, never from another replica's row (ADR-030 §8).
            groups: This replica's groups, whose members hold a server by
                reference and follow a replaced copy.
            group_rules: How this replica applies stored groups. None leaves
                groups to the replica that changed them.
            instance_id: This replica, whose own group events are not applied
                again. None takes this process's.
        """
        self._repository = repository
        self._configs = config_repository
        self._runner = runner
        self._closer = closer
        self._declared_in_config = declared_in_config
        self._groups = groups
        self._group_rules = group_rules
        self._instance_id = instance_id or current_instance_id()
        #: A peer's group events not applied yet: their rows could not be read.
        self._group_events: list[Any] = []
        #: Stored members whose server is not held here, by that server's id,
        #: with the groups that name them. Applied when it registers.
        self._awaiting_server: dict[str, set[str]] = {}
        #: Key-pinned servers whose row could not be read: retired and refusing
        #: calls, read again on each later event until a read succeeds (§8).
        self._unread: set[str] = set()
        self._serial = threading.Lock()

    def handle(self, event: DomainEvent) -> None:
        """Apply a registration, a deregistration, an update or a group change to the local fleet."""
        if isinstance(event, GROUP_EVENTS) and event.produced_by == self._instance_id:
            return
        # The tail and a request thread publishing live can both get here for
        # one server; two replacements would orphan the first, client and all.
        with self._serial:
            self._apply(event)

    def _apply(self, event: DomainEvent) -> None:
        for mcp_server_id in sorted(self._unread - {getattr(event, "mcp_server_id", None)}):
            if not self._apply_update(mcp_server_id):
                break  # The store is still unreachable; one failed read is enough per event.
        if isinstance(event, McpServerUpdated):
            self._apply_update(event.mcp_server_id)
        elif isinstance(event, McpServerRegistered):
            self._register(event.mcp_server_id, event.produced_by)
        elif isinstance(event, McpServerDeregistered):
            self._deregister(event.mcp_server_id, event.produced_by)
        elif isinstance(event, (EgressPolicySet, EgressPolicyCleared)):
            self._apply_l7(event.mcp_server_id)
        elif isinstance(event, GROUP_EVENTS):
            self._group_events.append(event)
        self._apply_group_events()

    def _apply_group_events(self) -> None:
        """Apply the peers' group events waiting here against the rows, or keep them until those can be read."""
        if self._group_rules is None or not self._group_events:
            return
        rows: Any = self._configs  # Both durable repositories keep the group rows too.
        try:
            stored = cast("StoredGroups", self._runner.run(rows.read_groups(), READ_TIMEOUT_S))
        except Exception as error:  # noqa: BLE001 -- fault-barrier: unreadable rows must not stall the tail
            logger.warning("fleet_projection_group_rows_unreadable", waiting=len(self._group_events), error=str(error))
            return
        events, self._group_events = self._group_events, []
        for event in events:
            try:
                self._apply_group_event(event, stored, self._group_rules)
            except Exception as error:  # noqa: BLE001 -- fault-barrier: one malformed row must not stall the others
                logger.warning("fleet_projection_group_row_malformed", group_id=event.group_id, error=str(error))

    def _apply_group_event(self, event: Any, stored: StoredGroups, rules: GroupRules) -> None:
        """Apply what *event* names and nothing else.

        The rows are a snapshot, and a request here may have changed the group
        since. So a member leaves only when an event names it, and a stored row
        adds only the member its event names.
        """
        group_id = event.group_id
        record = next((row for row in stored.groups if row.group_id == group_id), None)
        rows = tuple(row for row in stored.members if row.group_id == group_id)
        declared = rules.declared_members(group_id)
        held = (self._groups or {}).get(group_id)
        kept = {row.member_id for row in rows} | (declared or frozenset())
        if isinstance(event, GroupDeleted):
            if declared is None and record is None:
                if held is not None:
                    rules.drop(group_id)
            elif held is not None:
                self._drop_members(held, {str(member.id) for member in held.members} - kept)
        elif isinstance(event, GroupMemberRemoved):
            if held is not None and event.member_id not in kept:
                self._drop_members(held, {event.member_id})
        elif isinstance(event, GroupUpdated):
            if held is not None and record is not None and declared is None:
                held.adopt_config(record.strategy, record.description, record.min_healthy)
        elif record is not None or declared is not None:
            if isinstance(event, GroupMemberAdded):
                rows = tuple(row for row in rows if row.member_id == event.member_id)
            rules.restore(StoredGroups((record,) if record is not None else (), rows))
            held = (self._groups or {}).get(group_id)
            for row in rows:
                if (
                    held is not None
                    and not held.get_member(row.member_id)
                    and not self._repository.exists(row.member_id)
                ):
                    self._awaiting_server.setdefault(row.member_id, set()).add(group_id)
        logger.info("fleet_projection_group_applied", group_id=group_id, event_type=type(event).__name__)

    @staticmethod
    def _drop_members(group: McpServerGroup, member_ids: set[str]) -> None:
        with swapping():
            for member_id in member_ids:
                group.drop_member(member_id)

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

        names = self._peer_key_names()
        if names is None or self._contradicts(mcp_server_id, snapshot, names):
            return

        mcp_server = server_from_snapshot(snapshot)
        self._repository.add(mcp_server_id, mcp_server)
        # A member left vacant by its server's deletion holds this copy now,
        # if discovery registered it (#2107). Its group's events wait for the next drain: the projection
        # publishes nothing.
        fill_vacant_member(self._groups, mcp_server_id, mcp_server)
        for group_id in sorted(self._awaiting_server.pop(mcp_server_id, ())):
            self._group_events.append(
                GroupMemberAdded(group_id, mcp_server_id, weight=1, priority=1)
            )  # Never published.
        logger.info("fleet_projection_registered", mcp_server_id=mcp_server_id, produced_by=produced_by)

    def _apply_update(self, mcp_server_id: str) -> bool:
        """Apply a server's update made on another replica, or on this one (ADR-030 §8, #1955).

        A changed endpoint or `tls` block replaces the copy with a COLD one
        built from the row, and the old copy is retired; every replica does
        this, the one that handled the update included, because it compares
        against what the live client was built from, not against its updated
        aggregate. Other fields are taken in place. A key-pinned server whose
        row cannot be read, or that the peer-key name record contradicts, is
        retired: the tail has moved past the event and will not deliver it
        again, so keeping the old key list is not safe. A plain one keeps its
        copy until a later read succeeds.

        Returns:
            False when the store could not be read.
        """
        local = self._repository.get(mcp_server_id)
        declared = self._declared_in_config is not None and self._declared_in_config(mcp_server_id)
        if not isinstance(local, McpServer) or declared:
            self._unread.discard(mcp_server_id)
            return True
        local_pinned = isinstance(local._peer_identity, PeerIdentity)
        try:
            row = self._runner.run(self._configs.get(mcp_server_id), READ_TIMEOUT_S)
            names = self._runner.run(self._configs.get_peer_key_names(), READ_TIMEOUT_S)
        except Exception as error:  # noqa: BLE001 -- fault-barrier: one unreadable row must not stall the tail
            # Remembered whatever the local copy is: the row may be the one that
            # gives a plain server its key (§4f), and the tail will not repeat it.
            self._unread.add(mcp_server_id)
            if local_pinned:
                self._retire(local)
            logger.warning("fleet_projection_update_row_unreadable", mcp_server_id=mcp_server_id, error=str(error))
            return False
        if row is None:
            # No row: the deregistration that follows removes the copy.
            return True
        self._unread.discard(mcp_server_id)
        if self._contradicts(mcp_server_id, row, names):
            self._retire(local)
            return True
        if not local.is_retired and local.connection_declaration() == connection_declaration(row.endpoint, row.tls):
            local.adopt_config(row.description, row.env, row.idle_ttl_s, row.health_check_interval_s)
            return True
        replacement = server_from_snapshot(row)
        self._repository.add(mcp_server_id, replacement)
        for group in (self._groups or {}).values():
            group.replace_member_server(mcp_server_id, replacement)
        self._retire(local)
        logger.info(
            "fleet_projection_server_replaced", mcp_server_id=mcp_server_id, key_pinned=peer_key_claim(row) is not None
        )
        return True

    def _retire(self, mcp_server: McpServer) -> None:
        client = mcp_server.retire()
        if client is not None and self._closer is not None:
            self._closer.submit(_close(client))

    def _peer_key_names(self) -> dict[str, str] | None:
        try:
            return cast("dict[str, str]", self._runner.run(self._configs.get_peer_key_names(), READ_TIMEOUT_S))
        except Exception as error:  # noqa: BLE001 -- fault-barrier: an unreadable record must not stall the tail
            logger.warning("fleet_projection_peer_key_record_unreadable", error=str(error))
            return None

    @staticmethod
    def _contradicts(mcp_server_id: str, row: McpServerConfigSnapshot, names: dict[str, str]) -> bool:
        """A row under a recorded name that does not declare the recorded key: an older replica's plain write."""
        recorded = names.get(mcp_server_id)
        if recorded is None or peer_key_claim(row) == recorded:
            return False
        logger.warning("fleet_projection_refused_row_under_peer_key_name", mcp_server_id=mcp_server_id)
        return True

    def _deregister(self, mcp_server_id: str, produced_by: str) -> None:
        self._unread.discard(mcp_server_id)
        local = self._repository.get(mcp_server_id)
        if local is None:
            return
        if self._declared_in_config is not None and self._declared_in_config(mcp_server_id):
            # This replica's file declares it, as in `_apply_update`: it leaves
            # when the file does, never on another replica's deletion of a
            # server of the same name (#2107).
            logger.info("fleet_projection_deregistration_of_declared_server_ignored", mcp_server_id=mcp_server_id)
            return
        self._repository.remove(mcp_server_id)
        # No group routes to the dropped copy, and its client is closed: the
        # copy is out of the fleet, and a group that selected it would start it
        # where nothing manages it (#2107). Only here, where this replica held
        # the server: the replica that deleted or replaced it has already
        # removed it, and seeing it absent leaves its groups as they are.
        vacate_member(self._groups, mcp_server_id)
        if isinstance(local, McpServer):
            self._retire(local)
        logger.info("fleet_projection_deregistered", mcp_server_id=mcp_server_id, produced_by=produced_by)

    def _read(self, mcp_server_id: str) -> Any:
        try:
            return self._runner.run(self._configs.get(mcp_server_id), READ_TIMEOUT_S)
        except Exception as error:  # noqa: BLE001 -- fault-barrier: one unreadable row must not stall the tail
            logger.warning("fleet_projection_read_failed", mcp_server_id=mcp_server_id, error=str(error))
            return None


async def _close(client: Any) -> None:
    client.close()
