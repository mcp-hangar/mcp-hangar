"""The in-process half of an approval hold.

A `threading.Event` per held call, so a decision arriving on this instance
releases it immediately and from any thread.

This is deliberately only half of the mechanism. It cannot see a decision made
on another instance, and for a while it was the whole thing -- the note here
said multi-instance was "a Cloud MVP concern", and that tier was retired in
ADR-010 while the note outlived it. The other half is in `ApprovalGateService`,
which also watches the approval record: shared storage is what makes a decision
visible across instances, and this registry is the fast path for the common case
where the decision lands where the call is waiting.
"""

import asyncio
import threading
import time
from dataclasses import dataclass, field

#: How often a wait that watches for abandonment looks at the caller's cancel event (#2272).
#:
#: A `threading.Event` cannot be waited on together with another one, and the
#: cancel event is the caller's, so it cannot be made to set the hold's. The
#: wait is cut into steps this long instead: an abandonment is seen within one
#: step, with no storage read, and a decision still wakes it at once.
ABANDON_CHECK_INTERVAL_S = 0.05


class EitherSet(threading.Event):
    """Reads set while either of two events is set: what abandons a hold (#2279).

    A hold is abandoned when its batch is cancelled (``fail_fast``, a stop) or
    when the caller gives up on the request (a client cancel). Only ``is_set``
    is read on a hold's abandonment; nothing sets or clears this, and ``wait``
    polls, as the hold's own wait does.
    """

    def __init__(self, first: threading.Event, second: threading.Event) -> None:
        super().__init__()
        self._first = first
        self._second = second

    def is_set(self) -> bool:
        return self._first.is_set() or self._second.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return False
            time.sleep(ABANDON_CHECK_INTERVAL_S if remaining is None else min(ABANDON_CHECK_INTERVAL_S, remaining))
        return True


@dataclass
class _HoldEntry:
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    #: Set when the held call has been abandoned -- its batch was cancelled
    #: while it was held. The caller's event, not the registry's: the registry
    #: only reads it, so an approval can be refused for a call that will not run.
    abandoned: threading.Event | None = None


class ApprovalHoldRegistry:
    """Registry of pending approval holds keyed by approval_id."""

    def __init__(self) -> None:
        self._holds: dict[str, _HoldEntry] = {}
        self._lock = threading.Lock()

    async def register(self, approval_id: str, abandoned: threading.Event | None = None) -> None:
        """Register a new hold for the given approval_id.

        Args:
            approval_id: The held approval.
            abandoned: Set by the caller once it will no longer run the call.
        """
        with self._lock:
            self._holds[approval_id] = _HoldEntry(abandoned=abandoned)

    def is_abandoned(self, approval_id: str) -> bool:
        """Whether the call held under *approval_id* here has been abandoned by its caller.

        False for a hold this instance does not have: only the instance that
        holds the call knows whether it still will run it.
        """
        with self._lock:
            entry = self._holds.get(approval_id)
        return entry is not None and entry.abandoned is not None and entry.abandoned.is_set()

    async def resolve(self, approval_id: str, approved: bool) -> bool:
        """Set decision for a pending hold.

        Returns False if approval_id not found (already expired/cleaned up).
        """
        with self._lock:
            entry = self._holds.get(approval_id)
            if entry is None:
                return False
            entry.approved = approved
            entry.event.set()
            return True

    async def wait(self, approval_id: str, timeout_seconds: float) -> bool | None:
        """Wait for a local resolution, then release the hold.

        Returns:
            True if approved, False if denied, None on timeout.
        """
        try:
            return await self.wait_slice(approval_id, timeout_seconds)
        finally:
            self.release(approval_id)

    async def wait_slice(
        self, approval_id: str, timeout_seconds: float, *, until_abandoned: bool = False
    ) -> bool | None:
        """Wait up to `timeout_seconds` for a local resolution, keeping the hold.

        Separate from `wait` because the caller polls shared storage between
        slices to catch a decision made on another instance. Releasing the hold
        after each slice -- which `wait` does, correctly, at the end -- would
        throw away the fast local path on the first tick.

        Runs the blocking `threading.Event.wait()` via `asyncio.to_thread` so
        the event loop stays free for the REST handler that resolves it.

        With *until_abandoned*, the slice also ends, undecided, as soon as the
        caller abandons the call, so the abandonment can be recorded at once
        rather than at the next storage poll (#2272).

        Returns:
            True if approved, False if denied, None if this slice elapsed.
        """
        with self._lock:
            entry = self._holds.get(approval_id)
        if entry is None:
            return None
        abandoned = entry.abandoned if until_abandoned else None
        signaled = await asyncio.to_thread(_wait, entry.event, abandoned, float(timeout_seconds))
        return entry.approved if signaled else None

    def release(self, approval_id: str) -> None:
        """Forget a hold. Safe to call for one that is already gone."""
        with self._lock:
            self._holds.pop(approval_id, None)


def _wait(event: threading.Event, abandoned: threading.Event | None, timeout_seconds: float) -> bool:
    """Wait up to *timeout_seconds* for *event*; stop early once *abandoned* is set. Whether *event* is set."""
    if abandoned is None:
        return event.wait(timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
    while not abandoned.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if event.wait(min(ABANDON_CHECK_INTERVAL_S, remaining)):
            return True
    return event.is_set()
