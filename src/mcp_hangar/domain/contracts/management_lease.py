"""Which instance is allowed to manage the fleet.

Three replicas can all serve tool calls at once -- that part is stateless enough
to work today. What they cannot all do is *manage*: run discovery, garbage-
collect idle servers, take health decisions, deregister on a TTL. Those are
convergence loops, and three of them racing on one shared database produce
exactly the behaviour nobody can debug afterwards: a server registered by one
replica and deregistered by another, in the same second, forever.

So management is leased. One holder at a time, for a bounded period, renewed
while it lives and expiring on its own when the holder stops -- including when
the holder stops by dying, which is the case a graceful handover cannot cover.

**Not a Kubernetes `Lease`.** Core has to run on compose, on podman and from a
`pip install`, and a coordination primitive that only exists inside a cluster
would make those second-class. It lives in the storage backend the deployment
already chose, alongside everything else it persists.

**Not called failover.** `McpServerFailoverSaga` already exists and means
something entirely different -- moving traffic off an unhealthy *upstream*. Two
things named failover in one system is a debugging tax paid forever.

## The generation is the point

A TTL alone does not make a leader safe. The classic sequence:

1. A holds the lease and starts a deregistration sweep.
2. A stalls -- a long GC pause, a wedged disk, a network partition.
3. The lease expires. B acquires it and converges the fleet.
4. A wakes up, still believing it holds the lease, and finishes its sweep.

A's writes are from the past and they are about to undo B's work. The TTL did
not prevent this and cannot: A had no way to know time had passed. What prevents
it is the **generation** -- a number that increases every time the lease changes
hands, carried into the `WHERE` clause of every destructive write. A's write
names a generation that is no longer current, matches zero rows, and does
nothing at all. That is fencing, and it is the reason `acquire` returns one.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Lease:
    """A granted lease: who holds it, under which generation, until when."""

    holder: str
    """The instance holding it -- see `domain.events.producer`."""

    generation: int
    """Increases on every change of hands. Never on a renewal: a renewal is the
    same tenure continuing, and bumping it there would invalidate the holder's
    own in-flight writes."""

    expires_at: float
    """Unix time, **on the database's clock**. Replicas do not agree about the
    time and a lease compared against a local clock is a lease that expires
    early on a fast node and late on a slow one."""


class IManagementLease(ABC):
    """The single-holder lease that gates the management loops."""

    @abstractmethod
    def acquire(self, holder: str, ttl_s: float) -> Lease | None:
        """Take the lease if it is free or expired.

        Args:
            holder: This instance's identity.
            ttl_s: How long the tenure lasts without renewal.

        Returns:
            The granted lease, with the generation to fence writes by, or None
            if someone else holds an unexpired one.
        """

    @abstractmethod
    def renew(self, lease: Lease, ttl_s: float) -> Lease | None:
        """Extend a tenure that is still ours.

        Returns None when the lease has been lost -- expired and taken, or
        released -- which the caller must treat as "stop managing", not as an
        error to retry through.

        Args:
            lease: The lease previously granted to this instance.
            ttl_s: How much longer the tenure should last.

        Returns:
            The extended lease, same generation, or None if it is no longer ours.
        """

    @abstractmethod
    def release(self, lease: Lease) -> None:
        """Give up the lease, so a peer can take over now rather than in a TTL.

        Only affects the lease if it is still ours: a holder that was already
        deposed must not be able to release the *current* holder's tenure.

        Args:
            lease: The lease previously granted to this instance.
        """

    @abstractmethod
    def current(self) -> Lease | None:
        """Who holds it now, if anyone -- for diagnostics and for tests.

        Returns:
            The lease as stored, expired or not. Callers deciding whether they
            may manage must use `acquire`/`renew`, which do the comparison
            against the database's clock atomically; reading and then deciding
            is the race this exists to avoid.
        """
