"""Fail-closed ownership registry for MCP Tasks (tasks/* authorization).

A ``tools/call`` may return a task handle; the client then drives
``tasks/get`` / ``tasks/cancel`` / etc. by that handle. Because task handles
cannot be scoped to a session, the server MUST keep its own
``task_id -> owner`` map and authorize every ``tasks/*`` call. Otherwise a
caller could guess or replay another tenant's handle and read or cancel their
task, crossing tenant and principal boundaries.

This module provides the reusable, thread-safe primitive: a TTL- and
maxsize-bounded registry that binds each ``task_id`` to its owning
tenant/principal at creation and authorizes later access fail-closed
(unknown, expired, or mismatched -> deny). It mirrors the locking/TTL/LRU
style of ``_SuspendedSessionCache`` in ``server/api/sessions.py``.
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass

_REGISTRY_MAXSIZE = 100_000
_REGISTRY_TTL_S = 86_400.0  # 24 hours


@dataclass(frozen=True, slots=True)
class TaskOwner:
    """The owner identity bound to a task handle.

    ``tenant_id`` and ``principal_id`` may be ``None`` when the corresponding
    dimension is not established for the caller. A registered owner with
    ``principal_id=None`` authorizes any principal of the same tenant (see
    :meth:`TaskOwnershipRegistry.authorize`).
    """

    tenant_id: str | None
    principal_id: str | None


class TaskOwnershipRegistry:
    """Thread-safe, fail-closed registry binding task handles to their owner.

    Entries are TTL-bounded (default 24h) and maxsize-bounded with LRU
    eviction. Expired entries are evicted lazily on access and proactively on
    registration when the store is full.
    """

    def __init__(
        self,
        maxsize: int = _REGISTRY_MAXSIZE,
        ttl: float = _REGISTRY_TTL_S,
    ) -> None:
        self._maxsize: int = maxsize
        self._ttl: float = ttl
        # OrderedDict preserves insertion order for LRU-style eviction.
        self._store: collections.OrderedDict[str, tuple[TaskOwner, float]] = collections.OrderedDict()
        self._lock: threading.Lock = threading.Lock()

    def register(self, task_id: str, owner: TaskOwner) -> None:
        """Bind ``task_id`` to ``owner`` at task creation.

        Re-registering a known ``task_id`` refreshes its TTL and owner.

        Raises:
            ValueError: if ``task_id`` is empty.
        """
        if not task_id:
            raise ValueError("task_id must be a non-empty string")
        with self._lock:
            self._evict_expired_locked()
            if task_id in self._store:
                self._store.move_to_end(task_id)
                self._store[task_id] = (owner, time.monotonic())
                return
            if len(self._store) >= self._maxsize:
                # Evict the oldest entry.
                _ = self._store.popitem(last=False)
            self._store[task_id] = (owner, time.monotonic())

    def authorize(self, task_id: str, caller: TaskOwner) -> bool:
        """Return ``True`` only if ``caller`` may access ``task_id``.

        Fail-closed: returns ``False`` for an unknown or expired ``task_id``,
        or for any owner mismatch. Access is granted only when the caller's
        ``tenant_id`` equals the owner's ``tenant_id`` and, if the owner has a
        non-``None`` ``principal_id``, the caller's ``principal_id`` equals it.
        An owner registered with ``principal_id=None`` authorizes any principal
        of the same tenant.
        """
        if not task_id:
            return False
        with self._lock:
            entry = self._store.get(task_id)
            if entry is None:
                return False
            owner, ts = entry
            if time.monotonic() - ts > self._ttl:
                del self._store[task_id]
                return False
            if caller.tenant_id != owner.tenant_id:
                return False
            if owner.principal_id is not None and caller.principal_id != owner.principal_id:
                return False
            return True

    def discard(self, task_id: str) -> None:
        """Remove ``task_id`` from the registry if present."""
        with self._lock:
            _ = self._store.pop(task_id, None)

    def clear(self) -> None:
        """Remove all entries from the registry."""
        with self._lock:
            self._store.clear()

    def _evict_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [k for k, (_, ts) in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]
