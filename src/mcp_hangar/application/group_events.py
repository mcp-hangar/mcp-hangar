"""Draining a group's events, and where each of them belongs (#1410).

A group records events in two quite different registers, and until now both
left the aggregate through one door: the group CRUD handlers. Everything a
group raised while *serving* -- a member leaving or rejoining rotation, the
circuit opening or closing, the state that follows from those -- sat on the
aggregate until somebody edited the group. If nobody did, it sat there until
the process ended. That is why #1357 writes its gauge from the breaker's own
callback instead of from ``GroupCircuitOpened``: a subscriber to that event
never heard it on time, or at all.

So every path that makes a group record something drains it now, through
`publish_group_events`. Which leaves the question the batching had been hiding:
these events are not all the same kind of fact.

**A group's configuration is a fact about the group.** It was created, renamed,
given a member, had one taken away, was deleted. One operator did it once, it
is the same on every replica, and it belongs in the group's stream on the
shared log, which is where the CRUD handlers have always put it.

**A group's health is a fact about this replica.** Rotation and the circuit
breaker live in each replica's own `McpServerGroup`; three replicas serving one
group have three rotations and three breakers, and they disagree routinely --
that is #1358's whole subject, and #1380 is what it looks like when one
replica's view is read as the fleet's. "The circuit opened" is not true of the
group. It is true of this pod.

`REPLICA_LOCAL_GROUP_EVENTS` is that second register, and it is published with
`publish_local`: delivered here, to this replica's handlers, and deliberately
never appended to the shared log.

Not appending them is the conservative reading of #1358, which is open,
human-required and blocked on an ADR. It names two options, and says of the
first that it "puts breaker transitions on the event log, which brings the
commit-ordering and projection/effect taxonomy rules into scope". Writing them
to the log is the mechanism that option turns on. A P3 delivery bug is not
where that gets decided, and log rows are not easily unwritten once they are in
an operator's database. Keeping them local also means no peer can apply another
replica's breaker transitions by construction rather than by convention: the
events are not in the log, so there is nothing for a tail to read, whatever
anybody subscribes later.

Nothing is lost by it. Every subscriber these events have today -- logging,
metrics, alerts, the audit trail, the security handler -- is an `EFFECT`, and
an effect runs only on the replica that produced the event. The shared log
would carry them to no one.
"""

from __future__ import annotations

from typing import Any

from ..domain.events import CircuitBreakerStateChanged, DomainEvent
from ..domain.model.mcp_server_group import (
    GroupCircuitClosed,
    GroupCircuitOpened,
    GroupCreated,
    GroupDeleted,
    GroupMemberAdded,
    GroupMemberHealthChanged,
    GroupMemberRemoved,
    GroupStateChanged,
    GroupUpdated,
    McpServerGroup,
)
from ..stream_ids import MCP_SERVER_GROUP

#: What a group records about **this replica's** view of it: which members this
#: pod is routing to, and whether this pod's breaker is open. Published locally
#: and never appended to the shared log -- see the module docstring.
#:
#: `CircuitBreakerStateChanged` is here because a group records one for every
#: transition of its own breaker, carrying the group's id in `mcp_server_id`.
#: A server's is a different event about a different aggregate, and never
#: reaches this module.
REPLICA_LOCAL_GROUP_EVENTS: frozenset[type[DomainEvent]] = frozenset(
    {
        CircuitBreakerStateChanged,
        GroupCircuitClosed,
        GroupCircuitOpened,
        GroupMemberHealthChanged,
        GroupStateChanged,
    }
)

#: What a group records about **itself**: the configuration an operator gave
#: it. The same on every replica, so it goes in the group's stream on the
#: shared log, as it always has.
#:
#: Every event a group records is in exactly one of these two sets, so a new
#: one has to be sorted on purpose
#: (tests/unit/test_a_groups_events_go_out_on_the_path_that_raised_them.py).
#: Left out of both, it would be published locally and its record quietly lost.
SHARED_GROUP_EVENTS: frozenset[type[DomainEvent]] = frozenset(
    {
        GroupCreated,
        GroupDeleted,
        GroupMemberAdded,
        GroupMemberRemoved,
        GroupUpdated,
    }
)


def publish_group_events(event_bus: Any, group: McpServerGroup) -> None:
    """Drain *group* and publish each event where it belongs.

    Called by every path that makes a group record something: the CRUD
    handlers, the executor after it reports a member's outcome, the rebalance
    saga after it reports one, and both surfaces of ``rebalance()``. Draining
    on the path that raised the events is the fix for #1410; sorting them is
    the decision described in this module's docstring.

    `AggregateRoot.collect_events` takes each event exactly once, so a drain
    here cannot publish what another drain already took, and there is nothing
    left for the next one to publish a second time.

    Args:
        event_bus: The bus to publish through. Untyped for the same reason the
            CRUD handlers' own `event_bus` is: the callers hold it under two
            structurally identical but nominally unrelated interfaces, one in
            `domain.contracts` and one on the application context.
        group: The group to drain.
    """
    local: list[DomainEvent] = []
    shared: list[DomainEvent] = []
    for event in group.collect_events():
        # Anything unsorted goes to the log. A record that should have been
        # local is noise; one that should have been shared and was not is gone.
        (local if type(event) in REPLICA_LOCAL_GROUP_EVENTS else shared).append(event)

    for event in local:
        event_bus.publish_local(event)
    if shared:
        event_bus.publish_aggregate_events(MCP_SERVER_GROUP, group.id, shared)
