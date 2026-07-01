"""Fail-closed consent gate for MCP Tasks (mid-flight ``input_required``).

A ``tools/call`` may return a task handle that later transitions to
``input_required`` with an ``inputRequests`` map, answered in-band via
``tasks/update``. Consent is therefore no longer only at ``tools/call``: a
governance proxy must gate mid-flight input and reject ``tasks/update`` answers
that do not correspond to a genuinely-pending request. Otherwise a caller could
inject an unexpected answer against a task that never asked for input, crossing
the async consent boundary.

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

_GATE_MAXSIZE = 100_000
_GATE_TTL_S = 86_400.0  # 24 hours


class TaskConsentGate:
    """Thread-safe, fail-closed gate for mid-flight task input consent.

    Each pending consent is keyed by ``(task_id, input_key)``. Entries are
    TTL-bounded (default 24h) and maxsize-bounded with LRU eviction. Expired
    entries are evicted lazily on access and proactively on ``open`` when the
    store is full.
    """

    def __init__(
        self,
        maxsize: int = _GATE_MAXSIZE,
        ttl: float = _GATE_TTL_S,
    ) -> None:
        self._maxsize: int = maxsize
        self._ttl: float = ttl
        # OrderedDict preserves insertion order for LRU-style eviction.
        self._store: collections.OrderedDict[tuple[str, str], float] = collections.OrderedDict()
        self._lock: threading.Lock = threading.Lock()

    def open(self, task_id: str, input_key: str) -> None:
        """Record that ``task_id`` requires consent for ``input_key``.

        Called when a task enters ``input_required``. Re-opening a known
        ``(task_id, input_key)`` refreshes its TTL.

        Raises:
            ValueError: if ``task_id`` or ``input_key`` is empty.
        """
        if not task_id:
            raise ValueError("task_id must be a non-empty string")
        if not input_key:
            raise ValueError("input_key must be a non-empty string")
        key = (task_id, input_key)
        with self._lock:
            self._evict_expired_locked()
            if key in self._store:
                self._store.move_to_end(key)
                self._store[key] = time.monotonic()
                return
            if len(self._store) >= self._maxsize:
                # Evict the oldest entry.
                _ = self._store.popitem(last=False)
            self._store[key] = time.monotonic()

    def is_consent_pending(self, task_id: str, input_key: str) -> bool:
        """Return ``True`` while a consent for ``(task_id, input_key)`` is open.

        Fail-closed: returns ``False`` for an empty argument or an unknown or
        expired entry. Expired entries are evicted lazily on access.
        """
        if not task_id or not input_key:
            return False
        with self._lock:
            return self._is_pending_locked((task_id, input_key))

    def answer(self, task_id: str, input_key: str) -> bool:
        """Record a ``tasks/update`` answer for ``(task_id, input_key)``.

        Returns ``True`` only if a matching pending consent existed, then clears
        it so a second answer is rejected. Fail-closed: an unknown, expired, or
        already-answered consent returns ``False``, rejecting the mid-flight
        input as unexpected or injected.
        """
        if not task_id or not input_key:
            return False
        key = (task_id, input_key)
        with self._lock:
            if not self._is_pending_locked(key):
                return False
            del self._store[key]
            return True

    def discard(self, task_id: str) -> None:
        """Clear every pending consent for ``task_id``."""
        with self._lock:
            stale = [key for key in self._store if key[0] == task_id]
            for key in stale:
                del self._store[key]

    def clear(self) -> None:
        """Remove all entries from the gate."""
        with self._lock:
            self._store.clear()

    def _is_pending_locked(self, key: tuple[str, str]) -> bool:
        ts = self._store.get(key)
        if ts is None:
            return False
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return False
        return True

    def _evict_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [k for k, ts in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]
