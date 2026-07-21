"""Fail-closed digest guard for MCP Tasks (tool-schema pinning across completion).

A ``tools/call`` may return a task handle and complete later, out of band from
the synchronous invoke path. Hangar pins the per-tenant tool digest on the
synchronous call path, but a background task can complete against a tool whose
schema has since drifted. The digest observed at invoke time and the digest
observed at completion time MUST match; otherwise the completion is served
against a different tool contract than the caller authorized, breaking
supply-chain integrity across the async boundary.

Task ids are unique only *per upstream*, so the store keys on the composite
``TaskKey = (target_server_id, task_id)`` rather than the bare ``task_id``.

This module provides the reusable, thread-safe primitive: a TTL- and
maxsize-bounded guard that pins each :data:`TaskKey` to the tool digest
captured at task creation (invoke time) and re-verifies it fail-closed on
completion (unknown, expired, or mismatched -> deny). It mirrors the
locking/TTL/LRU style of
:class:`~mcp_hangar.domain.services.task_ownership.TaskOwnershipRegistry`.
"""

from __future__ import annotations

import collections
import threading
import time
from collections.abc import Callable

_GUARD_MAXSIZE = 100_000
_GUARD_TTL_S = 86_400.0  # 24 hours

# Composite store key: (target_server_id, task_id). Task ids are unique only
# per upstream, so the server id disambiguates them. Internal-only.
TaskKey = tuple[str, str]


class TaskDigestConflictError(ValueError):
    """Raised when a live key is re-pinned to a *different* digest.

    Re-pinning an existing, non-expired key to a new digest would let a task
    silently switch the tool contract it is verified against, so the guard
    fails closed rather than clobbering the original pin. Subclasses
    :class:`ValueError` so existing ``ValueError`` handling (e.g. empty-value
    validation) still catches it while callers that care can distinguish a
    re-pin conflict.
    """


class TaskDigestGuard:
    """Thread-safe, fail-closed guard binding task handles to their tool digest.

    Entries are TTL-bounded (default 24h) and maxsize-bounded with LRU
    eviction. Expired entries are evicted lazily on access; the store is
    capped by evicting the oldest entry on insertion when full. Whenever an
    entry is dropped by LRU cap or TTL expiry (but not by an explicit
    :meth:`discard`), the optional ``on_evict`` callback is invoked so a caller
    can fail-close a still-live task instead of letting it silently vanish.
    """

    def __init__(
        self,
        maxsize: int = _GUARD_MAXSIZE,
        ttl: float = _GUARD_TTL_S,
        on_evict: Callable[[TaskKey], None] | None = None,
    ) -> None:
        self._maxsize: int = maxsize
        self._ttl: float = ttl
        self._on_evict: Callable[[TaskKey], None] | None = on_evict
        # OrderedDict preserves insertion order for LRU-style eviction.
        self._store: collections.OrderedDict[TaskKey, tuple[str, float]] = collections.OrderedDict()
        self._lock: threading.Lock = threading.Lock()

    def pin(self, key: TaskKey, tool_digest: str) -> None:
        """Record ``tool_digest`` for ``key`` at task creation (invoke time).

        Re-pinning a live key with the *same* digest refreshes its TTL
        (idempotent). Re-pinning a live key with a *different* digest fails
        closed rather than clobbering the existing pin. A key whose entry has
        expired is treated as absent and re-pinned freshly.

        Raises:
            ValueError: if either component of ``key`` or ``tool_digest`` is empty.
            TaskDigestConflictError: if a live ``key`` is re-pinned with a
                different digest.
        """
        server_id, task_id = key
        if not server_id or not task_id:
            raise ValueError("key components (target_server_id, task_id) must be non-empty strings")
        if not tool_digest:
            raise ValueError("tool_digest must be a non-empty string")
        evicted: TaskKey | None = None
        with self._lock:
            existing = self._store.get(key)
            if existing is not None:
                cur_digest, ts = existing
                if time.monotonic() - ts <= self._ttl:
                    # Live entry: same digest refreshes TTL; different digest fails closed.
                    if cur_digest != tool_digest:
                        raise TaskDigestConflictError(f"task key {key!r} is already pinned to a different digest")
                    self._store.move_to_end(key)
                    self._store[key] = (tool_digest, time.monotonic())
                    return
                # Expired: drop silently and re-pin freshly.
                del self._store[key]
            if len(self._store) >= self._maxsize:
                # Evict the oldest entry to stay under the cap.
                evicted, _ = self._store.popitem(last=False)
            self._store[key] = (tool_digest, time.monotonic())
        if evicted is not None:
            self._fire_evict(evicted)

    def verify(self, key: TaskKey, observed_digest: str) -> bool:
        """Return ``True`` only if ``observed_digest`` matches the pinned digest.

        Fail-closed: returns ``False`` for an unknown or expired ``key``, an
        empty argument, or any digest mismatch. A match is required so that a
        task completes against the same tool contract that was pinned at invoke
        time. An entry found expired is evicted (firing ``on_evict``).
        """
        server_id, task_id = key
        if not server_id or not task_id or not observed_digest:
            return False
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            pinned_digest, ts = entry
            if time.monotonic() - ts > self._ttl:
                del self._store[key]
                # Fall through to fire on_evict outside the lock.
            else:
                return observed_digest == pinned_digest
        # Expired path: entry was just evicted above.
        self._fire_evict(key)
        return False

    def discard(self, key: TaskKey) -> None:
        """Remove ``key`` from the guard if present (no ``on_evict``).

        An explicit discard is a deliberate terminal removal, so it does not
        fire the eviction callback.
        """
        with self._lock:
            _ = self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all entries from the guard (no ``on_evict``)."""
        with self._lock:
            self._store.clear()

    def _fire_evict(self, key: TaskKey) -> None:
        """Best-effort eviction callback; never breaks the primitive."""
        cb = self._on_evict
        if cb is None:
            return
        try:
            cb(key)
        except Exception:  # noqa: BLE001 -- callback failures must not break the guard
            pass
