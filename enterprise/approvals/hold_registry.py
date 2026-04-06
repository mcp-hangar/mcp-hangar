"""In-process approval hold registry using threading.Event.

Single-instance mechanism for blocking tool execution until a human
decision arrives or the timeout expires.  Uses threading primitives
so that resolve() can safely signal from any thread.

Multi-instance (Redis pub/sub) is a Cloud MVP concern.
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

    async def wait(self, approval_id: str, timeout_seconds: int) -> bool | None:
        """Wait for a resolution on the given approval_id.

        Runs the blocking threading.Event.wait() in a thread via
        asyncio.to_thread so the event loop stays free for concurrent
        tasks (e.g. resolve() arriving from a REST handler).

        Returns:
            True if approved, False if denied, None on timeout.
        """
        with self._lock:
            entry = self._holds.get(approval_id)
        if entry is None:
            return None
        try:
            signaled = await asyncio.to_thread(
                entry.event.wait, float(timeout_seconds)
            )
            if signaled:
                return entry.approved
            return None
        finally:
            with self._lock:
                self._holds.pop(approval_id, None)
