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
from dataclasses import dataclass, field


@dataclass
class _HoldEntry:
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


class ApprovalHoldRegistry:
    """Registry of pending approval holds keyed by approval_id."""

    def __init__(self) -> None:
        self._holds: dict[str, _HoldEntry] = {}
        self._lock = threading.Lock()

    async def register(self, approval_id: str) -> None:
        """Register a new hold for the given approval_id."""
        with self._lock:
            self._holds[approval_id] = _HoldEntry()

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

    async def wait_slice(self, approval_id: str, timeout_seconds: float) -> bool | None:
        """Wait up to `timeout_seconds` for a local resolution, keeping the hold.

        Separate from `wait` because the caller polls shared storage between
        slices to catch a decision made on another instance. Releasing the hold
        after each slice -- which `wait` does, correctly, at the end -- would
        throw away the fast local path on the first tick.

        Runs the blocking `threading.Event.wait()` via `asyncio.to_thread` so
        the event loop stays free for the REST handler that resolves it.

        Returns:
            True if approved, False if denied, None if this slice elapsed.
        """
        with self._lock:
            entry = self._holds.get(approval_id)
        if entry is None:
            return None
        signaled = await asyncio.to_thread(entry.event.wait, float(timeout_seconds))
        return entry.approved if signaled else None

    def release(self, approval_id: str) -> None:
        """Forget a hold. Safe to call for one that is already gone."""
        with self._lock:
            self._holds.pop(approval_id, None)
