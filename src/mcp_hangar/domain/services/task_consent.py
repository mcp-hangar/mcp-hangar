"""Fail-closed consent gate for MCP Tasks (mid-flight ``input_required``).

A ``tools/call`` may return a task handle that later transitions to
``input_required`` with an ``inputRequests`` map, answered in-band via
``tasks/update``. Consent is therefore no longer only at ``tools/call``: a
governance proxy must gate mid-flight input and reject ``tasks/update`` answers
that do not correspond to a genuinely-pending request. Otherwise a caller could
inject an unexpected answer against a task that never asked for input, crossing
the async consent boundary.

Task ids are unique only *per upstream*, so the gate keys each pending consent
on the composite ``TaskKey = (target_server_id, task_id)`` plus the
``input_key`` rather than the bare ``task_id``; two upstreams may legitimately
mint the same ``task_id``.

This module provides the reusable, thread-safe primitive: a TTL- and
maxsize-bounded gate that records each pending consent when a task enters
``input_required`` and clears it fail-closed on the matching ``tasks/update``
answer (unknown, expired, or already-answered -> reject). It mirrors the
locking/TTL/LRU style of
:class:`~mcp_hangar.domain.services.task_ownership.TaskOwnershipRegistry`.
"""

from __future__ import annotations

import collections
import threading
import time
from collections.abc import Callable

_GATE_MAXSIZE = 100_000
_GATE_TTL_S = 86_400.0  # 24 hours

# Composite task key: (target_server_id, task_id). Task ids are unique only
# per upstream, so the server id disambiguates them. Internal-only.
TaskKey = tuple[str, str]

# Full store key for one pending consent: (target_server_id, task_id, input_key).
# This is also the shape handed to ``on_evict``. Internal-only.
ConsentKey = tuple[str, str, str]


class TaskConsentGate:
    """Thread-safe, fail-closed gate for mid-flight task input consent.

    Each pending consent is keyed by ``(target_server_id, task_id, input_key)``.
    Entries are TTL-bounded (default 24h) and maxsize-bounded with LRU eviction.
    Expired entries are evicted lazily on access; the store is capped by evicting
    the oldest entry on insertion when full. Whenever an entry is dropped by LRU
    cap or TTL expiry (but not by an explicit :meth:`discard` or :meth:`clear`),
    the optional ``on_evict`` callback is invoked with the full consent key so a
    caller can fail-close a still-live consent instead of letting it silently
    vanish.
    """

    def __init__(
        self,
        maxsize: int = _GATE_MAXSIZE,
        ttl: float = _GATE_TTL_S,
        on_evict: Callable[[ConsentKey], None] | None = None,
    ) -> None:
        self._maxsize: int = maxsize
        self._ttl: float = ttl
        self._on_evict: Callable[[ConsentKey], None] | None = on_evict
        # OrderedDict preserves insertion order for LRU-style eviction.
        self._store: collections.OrderedDict[ConsentKey, float] = collections.OrderedDict()
        self._lock: threading.Lock = threading.Lock()

    def open(self, task_key: TaskKey, input_key: str) -> None:
        """Record that ``task_key`` requires consent for ``input_key``.

        Called when a task enters ``input_required``. Re-opening a live
        ``(target_server_id, task_id, input_key)`` refreshes its TTL. A key
        whose entry has expired is treated as absent and re-opened freshly.

        Raises:
            ValueError: if any component of ``task_key`` or ``input_key`` is empty.
        """
        server_id, task_id = task_key
        if not server_id or not task_id:
            raise ValueError("task_key components (target_server_id, task_id) must be non-empty strings")
        if not input_key:
            raise ValueError("input_key must be a non-empty string")
        key: ConsentKey = (server_id, task_id, input_key)
        evicted: ConsentKey | None = None
        with self._lock:
            ts = self._store.get(key)
            if ts is not None:
                if time.monotonic() - ts <= self._ttl:
                    # Live entry: refresh TTL.
                    self._store.move_to_end(key)
                    self._store[key] = time.monotonic()
                    return
                # Expired: drop silently and re-open freshly.
                del self._store[key]
            if len(self._store) >= self._maxsize:
                # Evict the oldest entry to stay under the cap.
                evicted, _ = self._store.popitem(last=False)
            self._store[key] = time.monotonic()
        if evicted is not None:
            self._fire_evict(evicted)

    def is_consent_pending(self, task_key: TaskKey, input_key: str) -> bool:
        """Return ``True`` while a consent for the composite key is open.

        Fail-closed: returns ``False`` for an empty argument or an unknown or
        expired entry. An entry found expired is evicted lazily on access
        (firing ``on_evict``).
        """
        server_id, task_id = task_key
        if not server_id or not task_id or not input_key:
            return False
        key: ConsentKey = (server_id, task_id, input_key)
        with self._lock:
            ts = self._store.get(key)
            if ts is None:
                return False
            if time.monotonic() - ts > self._ttl:
                del self._store[key]
                # Fall through to fire on_evict outside the lock.
            else:
                return True
        # Expired path: entry was just evicted above.
        self._fire_evict(key)
        return False

    def answer(self, task_key: TaskKey, input_key: str) -> bool:
        """Record a ``tasks/update`` answer for the composite key.

        Returns ``True`` only if a matching pending consent existed, then clears
        it so a second answer is rejected. Fail-closed: an unknown, expired, or
        already-answered consent returns ``False``, rejecting the mid-flight
        input as unexpected or injected. An entry found expired is evicted
        (firing ``on_evict``).
        """
        server_id, task_id = task_key
        if not server_id or not task_id or not input_key:
            return False
        key: ConsentKey = (server_id, task_id, input_key)
        with self._lock:
            ts = self._store.get(key)
            if ts is None:
                return False
            if time.monotonic() - ts > self._ttl:
                del self._store[key]
                # Fall through to fire on_evict outside the lock, then reject.
            else:
                del self._store[key]
                return True
        # Expired path: entry was just evicted above.
        self._fire_evict(key)
        return False

    def discard(self, task_key: TaskKey) -> None:
        """Clear every pending consent for ``task_key`` (no ``on_evict``).

        An explicit discard is a deliberate terminal removal, so it does not
        fire the eviction callback.
        """
        server_id, task_id = task_key
        with self._lock:
            stale = [k for k in self._store if k[0] == server_id and k[1] == task_id]
            for k in stale:
                del self._store[k]

    def clear(self) -> None:
        """Remove all entries from the gate (no ``on_evict``)."""
        with self._lock:
            self._store.clear()

    def _fire_evict(self, key: ConsentKey) -> None:
        """Best-effort eviction callback; never breaks the gate."""
        cb = self._on_evict
        if cb is None:
            return
        try:
            cb(key)
        except Exception:  # noqa: BLE001 -- callback failures must not break the gate
            pass
