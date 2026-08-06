"""Holding the management lease, and knowing when we have stopped holding it.

The lease itself (`domain.contracts.management_lease`) is a row. This is the
thing that keeps it: acquires when it is free, renews while it lives, and --
the part that matters -- decides that it has been lost, promptly and on the
safe side.

## Losing it has two shapes, and only one of them is an answer

`renew` returning None is the easy one: the database says the tenure is over,
so management stops. The hard one is the database not answering at all. A
network blip, a failover on the storage side, a pool that has run dry: the
renewal raises and this instance learns nothing about whether it still holds
anything.

Retrying through that is the wrong instinct. The lease is expiring on the
database's clock whether or not we can read it, and once it lapses a peer will
take it and start converging the fleet. An instance that keeps managing while
it cannot prove it holds the lease is exactly the stalled leader the generation
exists to fence -- and fencing is a last line, not a plan.

So there is a **renew deadline**, measured locally with a monotonic clock and
deliberately shorter than the TTL: if this instance has not had a successful
renewal within it, it declares the lease lost on its own and stops managing,
without waiting to be told. It gives up slightly early rather than slightly
late, which is the direction where the failure mode is "nobody manages for a
few seconds" instead of "two instances manage at once".

The defaults mirror Kubernetes leader election, for the ordinary reason that
its trade-off between failover speed and flapping under a stop-the-world pause
has been argued about by more people than this one has: 15s tenure, renewed
every 5s, given up if 10s pass without a successful renewal.
"""

from __future__ import annotations

from collections.abc import Callable
import threading
import time

from mcp_hangar.domain.contracts.management_lease import IManagementLease, Lease
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

#: How long a tenure lasts without renewal. Shorter means a dead leader is
#: replaced sooner; it also means a garbage-collection pause is likelier to cost
#: a live one its lease, and a fleet whose manager keeps changing converges
#: worse than one whose manager is occasionally slow.
DEFAULT_TTL_S = 15.0

#: How often to try. Both for renewing a held lease and for acquiring a free
#: one, so a dead leader is noticed within roughly this interval plus the
#: remainder of its TTL.
DEFAULT_INTERVAL_S = 5.0

#: How long this instance will go without a *successful* renewal before it
#: declares the lease lost by itself. Under the TTL on purpose -- see the module
#: docstring; the gap is what keeps two managers from overlapping.
DEFAULT_RENEW_DEADLINE_S = 10.0


class ManagementLeaseKeeper:
    """Acquires and holds the management lease, on a thread of its own."""

    def __init__(
        self,
        lease_store: IManagementLease,
        holder: str,
        *,
        ttl_s: float = DEFAULT_TTL_S,
        interval_s: float = DEFAULT_INTERVAL_S,
        renew_deadline_s: float = DEFAULT_RENEW_DEADLINE_S,
        on_acquired: Callable[[Lease], None] | None = None,
        on_lost: Callable[[], None] | None = None,
    ) -> None:
        """Create a keeper. Does not start it.

        Args:
            lease_store: The backend's lease.
            holder: This instance's identity, from `domain.events.producer`.
            ttl_s: Tenure length.
            interval_s: How often to renew or retry acquiring.
            renew_deadline_s: How long without a successful renewal before the
                lease is presumed lost. Must be under `ttl_s`.
            on_acquired: Called once per tenure, when it begins.
            on_lost: Called once per tenure, when it ends.

        Raises:
            ValueError: If the deadline is not under the TTL -- a deadline at or
                past the TTL means this instance can still be managing at the
                moment a peer is entitled to take over, which is the one thing
                the whole arrangement is for.
        """
        if renew_deadline_s >= ttl_s:
            raise ValueError(
                f"renew_deadline_s ({renew_deadline_s}) must be under ttl_s ({ttl_s}): "
                "an instance that gives up no earlier than the lease expires can still be managing "
                "when a peer acquires it"
            )
        self._store = lease_store
        self._holder = holder
        self._ttl_s = ttl_s
        self._interval_s = interval_s
        self._renew_deadline_s = renew_deadline_s
        self._on_acquired = on_acquired
        self._on_lost = on_lost

        self._guard = threading.Lock()
        self._lease: Lease | None = None
        self._last_success: float = 0.0
        self._running = False
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def lease(self) -> Lease | None:
        """The tenure this instance currently believes it holds, if any."""
        with self._guard:
            return self._lease

    def may_manage(self) -> bool:
        """Whether the management loops may run right now.

        Read per cycle rather than once at startup: a lease lost mid-life has to
        stop the *next* cycle, not the next process.
        """
        return self.lease is not None

    def start(self) -> None:
        """Start acquiring and holding the lease."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="management-lease", daemon=True)
        self._thread.start()
        logger.info(
            "management_lease_keeper_started",
            holder=self._holder,
            ttl_s=self._ttl_s,
            interval_s=self._interval_s,
        )

    def stop(self) -> None:
        """Stop, releasing the lease so a peer takes over in seconds.

        Releasing is the difference between a rolling update that pauses
        management for a moment and one that pauses it for a TTL per pod.
        """
        self._running = False
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=self._interval_s + 1.0)

        with self._guard:
            lease, self._lease = self._lease, None
        if lease is not None:
            try:
                self._store.release(lease)
            except Exception as error:  # noqa: BLE001 -- fault-barrier: shutdown must finish even if the release fails
                # Costs a peer the wait for the TTL. Refusing to shut down over
                # it would cost more.
                logger.warning("management_lease_release_failed", error=str(error))
            self._announce_lost()

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as error:  # noqa: BLE001 -- fault-barrier: the keeper must outlive any single failure
                logger.warning("management_lease_tick_failed", error=str(error))
            self._wake.wait(timeout=self._interval_s)

    def _tick(self) -> None:
        """One pass: hold on to the lease, or try to take it."""
        if self.lease is None:
            self._try_acquire()
        else:
            self._try_renew()

    def _try_acquire(self) -> None:
        try:
            lease = self._store.acquire(self._holder, self._ttl_s)
        except Exception as error:  # noqa: BLE001 -- fault-barrier: an unreachable store means "not the manager", not a crash
            # Nothing to give up: an instance that never held the lease and
            # cannot reach the store simply is not managing.
            logger.debug("management_lease_acquire_failed", error=str(error))
            return
        if lease is None:
            return
        with self._guard:
            self._lease = lease
            self._last_success = time.monotonic()
        if self._on_acquired is not None:
            self._on_acquired(lease)

    def _try_renew(self) -> None:
        held = self.lease
        if held is None:
            return
        try:
            renewed = self._store.renew(held, self._ttl_s)
        except Exception as error:  # noqa: BLE001 -- fault-barrier: an unreachable store is handled by the deadline below
            self._give_up_if_past_deadline(str(error))
            return

        if renewed is None:
            # A definite answer: the tenure is over. Nothing to release -- it
            # belongs to someone else now, and releasing it would take the lease
            # away from whoever holds it.
            self._lose("the lease was taken or released")
            return

        with self._guard:
            self._lease = renewed
            self._last_success = time.monotonic()

    def _give_up_if_past_deadline(self, error: str) -> None:
        """Presume the lease lost when it can no longer be proven held.

        The database is unreachable, so there is no answer to be had -- but the
        tenure is expiring on its clock regardless, and a peer will take it. The
        deadline is measured with a monotonic clock because this is exactly the
        moment when a wall clock might step.
        """
        with self._guard:
            elapsed = time.monotonic() - self._last_success
        if elapsed < self._renew_deadline_s:
            logger.debug("management_lease_renew_failed", error=error, elapsed_s=round(elapsed, 1))
            return
        self._lose(f"no successful renewal in {elapsed:.1f}s and the store is unreachable: {error}")

    def _lose(self, reason: str) -> None:
        with self._guard:
            lease, self._lease = self._lease, None
        if lease is None:
            return
        logger.warning(
            "management_lease_lost",
            holder=self._holder,
            generation=lease.generation,
            reason=reason,
            detail="management loops stop until this instance acquires it again",
        )
        self._announce_lost()

    def _announce_lost(self) -> None:
        if self._on_lost is None:
            return
        try:
            self._on_lost()
        except Exception as error:  # noqa: BLE001 -- fault-barrier: a listener must not keep the keeper holding a lost lease
            logger.warning("management_lease_on_lost_failed", error=str(error))
