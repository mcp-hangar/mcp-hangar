"""Fail-closed ownership registry for MCP Tasks (tasks/* authorization).

A ``tools/call`` may return a task handle; the client then drives
``tasks/get`` / ``tasks/cancel`` / etc. by that handle. Because task handles
cannot be scoped to a session, the server MUST keep its own
``task -> owner`` map and authorize every ``tasks/*`` call. Otherwise a
caller could guess or replay another tenant's handle and read or cancel their
task, crossing tenant and principal boundaries.

Task ids are unique only *per upstream*, so the store keys on the composite
``TaskKey = (target_server_id, task_id)`` rather than the bare ``task_id``;
two upstreams may legitimately mint the same ``task_id``.

This module provides the reusable, thread-safe primitive: a TTL- and
maxsize-bounded registry that binds each :data:`TaskKey` to its owning
tenant/principal at creation and authorizes later access fail-closed
(unknown, expired, or mismatched -> deny). It mirrors the locking/TTL/LRU
style of ``_SuspendedSessionCache`` in ``server/api/sessions.py``.
"""

from __future__ import annotations

import collections
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

_REGISTRY_MAXSIZE = 100_000
_REGISTRY_TTL_S = 86_400.0  # 24 hours

# Composite store key: (target_server_id, task_id). Task ids are unique only
# per upstream, so the server id disambiguates them. Internal-only.
TaskKey = tuple[str, str]


class TaskOwnerConflictError(ValueError):
    """Raised when a live key is re-registered to a *different* owner.

    Re-binding an existing, non-expired key to a new owner would let a later
    caller silently steal a task handle, so the registry fails closed rather
    than clobbering the original binding. Subclasses :class:`ValueError` so
    existing ``ValueError`` handling (e.g. empty-key validation) still catches
    it while callers that care can distinguish a rebind conflict.
    """


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
    eviction. Expired entries are evicted lazily on access; the store is
    capped by evicting the oldest entry on insertion when full. Whenever an
    entry is dropped by LRU cap or TTL expiry (but not by an explicit
    :meth:`discard`), the optional ``on_evict`` callback is invoked so a caller
    can fail-close a still-live task instead of letting it silently vanish.
    """

    def __init__(
        self,
        maxsize: int = _REGISTRY_MAXSIZE,
        ttl: float = _REGISTRY_TTL_S,
        on_evict: Callable[[TaskKey], None] | None = None,
    ) -> None:
        self._maxsize: int = maxsize
        self._ttl: float = ttl
        self._on_evict: Callable[[TaskKey], None] | None = on_evict
        # OrderedDict preserves insertion order for LRU-style eviction.
        self._store: collections.OrderedDict[TaskKey, tuple[TaskOwner, float]] = collections.OrderedDict()
        self._lock: threading.Lock = threading.Lock()

    def register(self, key: TaskKey, owner: TaskOwner) -> None:
        """Bind ``key`` to ``owner`` at task creation.

        Re-registering a live key with the *same* owner refreshes its TTL
        (idempotent). Re-registering a live key with a *different* owner fails
        closed rather than clobbering the existing binding. A key whose entry
        has expired is treated as absent and re-bound freshly.

        Raises:
            ValueError: if either component of ``key`` is empty.
            TaskOwnerConflictError: if a live ``key`` is re-registered with a
                different owner.
        """
        server_id, task_id = key
        if not server_id or not task_id:
            raise ValueError("key components (target_server_id, task_id) must be non-empty strings")
        evicted: TaskKey | None = None
        with self._lock:
            existing = self._store.get(key)
            if existing is not None:
                cur_owner, ts = existing
                if time.monotonic() - ts <= self._ttl:
                    # Live entry: same owner refreshes TTL; different owner fails closed.
                    if cur_owner != owner:
                        raise TaskOwnerConflictError(
                            f"task key {key!r} is already bound to a different owner"
                        )
                    self._store.move_to_end(key)
                    self._store[key] = (owner, time.monotonic())
                    return
                # Expired: drop silently and re-bind freshly (same key, new task binding).
                del self._store[key]
            if len(self._store) >= self._maxsize:
                # Evict the oldest entry to stay under the cap.
                evicted, _ = self._store.popitem(last=False)
            self._store[key] = (owner, time.monotonic())
        if evicted is not None:
            self._fire_evict(evicted)

    def authorize(self, key: TaskKey, caller: TaskOwner) -> bool:
        """Return ``True`` only if ``caller`` may access ``key``.

        Fail-closed: returns ``False`` for an unknown or expired ``key``, or
        for any owner mismatch. Access is granted only when the caller's
        ``tenant_id`` equals the owner's ``tenant_id`` and, if the owner has a
        non-``None`` ``principal_id``, the caller's ``principal_id`` equals it.
        An owner registered with ``principal_id=None`` authorizes any principal
        of the same tenant. An entry found expired is evicted (firing
        ``on_evict``).
        """
        server_id, task_id = key
        if not server_id or not task_id:
            return False
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            owner, ts = entry
            if time.monotonic() - ts > self._ttl:
                del self._store[key]
                # Fall through to fire on_evict outside the lock.
            else:
                if caller.tenant_id != owner.tenant_id:
                    return False
                if owner.principal_id is not None and caller.principal_id != owner.principal_id:
                    return False
                return True
        # Expired path: entry was just evicted above.
        self._fire_evict(key)
        return False

    def discard(self, key: TaskKey) -> None:
        """Remove ``key`` from the registry if present (no ``on_evict``).

        An explicit discard is a deliberate terminal removal, so it does not
        fire the eviction callback.
        """
        with self._lock:
            _ = self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all entries from the registry (no ``on_evict``)."""
        with self._lock:
            self._store.clear()

    def _fire_evict(self, key: TaskKey) -> None:
        """Best-effort eviction callback; never breaks the primitive."""
        cb = self._on_evict
        if cb is None:
            return
        try:
            cb(key)
        except Exception:  # noqa: BLE001 -- callback failures must not break the registry
            pass
