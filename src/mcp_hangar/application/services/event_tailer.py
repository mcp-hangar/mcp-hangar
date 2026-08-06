"""Following the shared log, so a replica knows what its peers did.

Without this, a gateway with three replicas has three separate views of one
fleet. A server started on A is invisible to B's tool catalogue; a risk signal
seen by C never reaches the other two. Each replica knows only the work it
happened to do, and which work that is depends on where the load balancer sent
each request.

So each replica follows the log and applies what it finds. Three properties make
that safe, and each of them was built before this:

**It skips its own events.** A replica publishes locally *and* appends to the
log it is tailing, so without the producer on the row (#792) it would deliver
everything it did a second time -- and idempotent handlers would hide that,
right up until one of them was not.

**It delivers to projections only.** An effect belongs to the instance that
produced the event (#798), so running effects here is how three replicas send
three copies of every audit record. `deliver_tailed` enforces it; this is only
the thing that calls it.

**It resumes from a cursor the store defines** (#793), not from a position. On
PostgreSQL a position cursor loses events that commit out of allocation order --
measured, not theoretical.

The cursor is **ephemeral**: it starts at the log head and dies with the pod.
There is no durable row and nothing to sweep, because the replica's view is
rebuilt from a snapshot plus the tail on every start. The head is taken
*before* the snapshot is read, so an event landing between the two is delivered
rather than falling in the gap between "not in the snapshot yet" and "before my
cursor".
"""

from __future__ import annotations

import threading

from mcp_hangar.domain.contracts.event_store import IEventStore, TailCursor
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

#: How often to look. A replica's view of its peers lags by up to this much,
#: which is the cost of not having every replica push to every other one.
DEFAULT_INTERVAL_S = 2.0

#: How many events one read may return. A replica that has been unable to reach
#: the database for a while must not pull an unbounded batch into memory when it
#: comes back.
DEFAULT_BATCH = 500


class EventTailer:
    """Reads the shared log and applies a peer's events to local projections."""

    def __init__(
        self,
        event_store: IEventStore,
        event_bus: object,
        instance_id: str,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        batch: int = DEFAULT_BATCH,
    ) -> None:
        """Create a tailer and **take its starting cursor now**.

        The cursor is captured at construction rather than at `start()`, because
        the caller has to be able to place that moment: it must come before the
        fleet snapshot is read. Anything appended after this instant is
        delivered; anything before it is already in the snapshot.

        Args:
            event_store: The shared log.
            event_bus: Where tailed events are delivered -- `deliver_tailed`.
            instance_id: This process's identity, so its own appends are skipped.
            interval_s: How often to read.
            batch: Maximum events per read.
        """
        self._store = event_store
        self._bus = event_bus
        self._instance_id = instance_id
        self._interval_s = interval_s
        self._batch = batch

        self._cursor = event_store.tail_head()
        self._running = False
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        logger.info("event_tailer_created", instance_id=instance_id, cursor=str(self._cursor))

    @property
    def cursor(self) -> TailCursor:
        """Where the tail has got to. For diagnostics and for tests."""
        return self._cursor

    def start(self) -> None:
        """Begin following the log."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="event-tailer", daemon=True)
        self._thread.start()
        logger.info("event_tailer_started", interval_s=self._interval_s)

    def stop(self) -> None:
        """Stop following. The cursor dies with the process, as intended."""
        self._running = False
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=self._interval_s + 1.0)
        logger.info("event_tailer_stopped")

    def _loop(self) -> None:
        while self._running:
            try:
                self.tick()
            except Exception as error:  # noqa: BLE001 -- fault-barrier: a replica must keep following after one bad read
                # Not fatal, and not silent. A tailer that stopped would leave
                # this replica's view frozen at whatever it last saw, serving
                # confidently from it.
                logger.warning("event_tailer_read_failed", error=str(error))
            self._wake.wait(timeout=self._interval_s)

    def tick(self) -> int:
        """Read one batch and apply it. Returns how many events were applied.

        Public because a test that drives the loop by hand is worth more than
        one that waits on a thread, and because a diagnostic endpoint may want
        to pull the tail forward on demand.
        """
        batch, cursor = self._store.read_since(self._cursor, self._batch)
        applied = 0
        skipped = 0

        for _stream_id, event in batch:
            if event.produced_by == self._instance_id:
                # Already delivered, when it was published here. Delivering it
                # again would double every projection this replica keeps.
                skipped += 1
                continue
            try:
                self._bus.deliver_tailed(event)  # type: ignore[attr-defined]
                applied += 1
            except Exception as error:  # noqa: BLE001 -- fault-barrier: one bad event must not stall the tail
                # The cursor still advances past it. Stopping here would wedge
                # the replica's whole view behind one event it cannot apply,
                # and the bus already fault-barriers each individual handler.
                logger.warning(
                    "event_tailer_apply_failed",
                    event_type=type(event).__name__,
                    event_id=event.event_id,
                    error=str(error),
                )

        self._cursor = cursor
        if applied or skipped:
            logger.debug("event_tailer_applied", applied=applied, skipped_own=skipped, cursor=str(cursor))
        return applied
