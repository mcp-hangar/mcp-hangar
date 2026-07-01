"""Fail-closed digest guard for MCP Tasks (tool-schema pinning across completion).

A ``tools/call`` may return a task handle and complete later, out of band from
the synchronous invoke path. Hangar pins the per-tenant tool digest on the
synchronous call path, but a background task can complete against a tool whose
schema has since drifted. The digest observed at invoke time and the digest
observed at completion time MUST match; otherwise the completion is served
against a different tool contract than the caller authorized, breaking
supply-chain integrity across the async boundary.

This module provides the reusable, thread-safe primitive: a TTL- and
maxsize-bounded guard that pins each ``task_id`` to the tool digest captured at
task creation (invoke time) and re-verifies it fail-closed on completion
(unknown, expired, or mismatched -> deny). It mirrors the locking/TTL/LRU style
of :class:`~mcp_hangar.domain.services.task_ownership.TaskOwnershipRegistry`.
"""

from __future__ import annotations

import collections
import threading
import time

_GUARD_MAXSIZE = 100_000
_GUARD_TTL_S = 86_400.0  # 24 hours


class TaskDigestGuard:
    """Thread-safe, fail-closed guard binding task handles to their tool digest.

    Entries are TTL-bounded (default 24h) and maxsize-bounded with LRU
    eviction. Expired entries are evicted lazily on access and proactively on
    pinning when the store is full.
    """

    def __init__(
        self,
        maxsize: int = _GUARD_MAXSIZE,
        ttl: float = _GUARD_TTL_S,
    ) -> None:
        self._maxsize: int = maxsize
        self._ttl: float = ttl
        # OrderedDict preserves insertion order for LRU-style eviction.
        self._store: collections.OrderedDict[str, tuple[str, float]] = collections.OrderedDict()
        self._lock: threading.Lock = threading.Lock()

    def pin(self, task_id: str, tool_digest: str) -> None:
        """Record ``tool_digest`` for ``task_id`` at task creation (invoke time).

        Re-pinning a known ``task_id`` refreshes its TTL and digest.

        Raises:
            ValueError: if ``task_id`` or ``tool_digest`` is empty.
        """
        if not task_id:
            raise ValueError("task_id must be a non-empty string")
        if not tool_digest:
            raise ValueError("tool_digest must be a non-empty string")
        with self._lock:
            self._evict_expired_locked()
            if task_id in self._store:
                self._store.move_to_end(task_id)
                self._store[task_id] = (tool_digest, time.monotonic())
                return
            if len(self._store) >= self._maxsize:
                # Evict the oldest entry.
                _ = self._store.popitem(last=False)
            self._store[task_id] = (tool_digest, time.monotonic())

    def verify(self, task_id: str, observed_digest: str) -> bool:
        """Return ``True`` only if ``observed_digest`` matches the pinned digest.

        Fail-closed: returns ``False`` for an unknown or expired ``task_id``, an
        empty argument, or any digest mismatch. A match is required so that a
        task completes against the same tool contract that was pinned at invoke
        time.
        """
        if not task_id or not observed_digest:
            return False
        with self._lock:
            entry = self._store.get(task_id)
            if entry is None:
                return False
            pinned_digest, ts = entry
            if time.monotonic() - ts > self._ttl:
                del self._store[task_id]
                return False
            return observed_digest == pinned_digest

    def discard(self, task_id: str) -> None:
        """Remove ``task_id`` from the guard if present."""
        with self._lock:
            _ = self._store.pop(task_id, None)

    def clear(self) -> None:
        """Remove all entries from the guard."""
        with self._lock:
            self._store.clear()

    def _evict_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [k for k, (_, ts) in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]
