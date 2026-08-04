"""In-memory adapter for ISessionSuspensionRegistry.

Moved here from `server/api/sessions.py`, where it sat as a module-private
global beside the HTTP routes. It was never route-handling code: it is a
bounded, thread-safe, TTL-expiring store, which is an adapter.

The bound is not decoration. `tests/security/test_w4_suspended_sessions_bounded`
exists because an unbounded suspended-session set is a memory-growth channel an
attacker controls -- every suspension is triggered by traffic they can generate.
Keep `maxsize` and the eviction, and keep them tested, in any replacement.
"""

from __future__ import annotations

import collections
import threading
import time

_CACHE_MAXSIZE = 10_000
_CACHE_TTL_S = 86_400.0  # 24 hours


class InMemorySessionSuspensionRegistry:
    """Thread-safe TTL-bounded registry of suspended session IDs.

    Evicts expired entries lazily on access and proactively on add when full.

    Thread safety is load-bearing rather than defensive: the HTTP routes and the
    detection-enforcement event handler both reach this, from different threads.
    """

    def __init__(self, maxsize: int = _CACHE_MAXSIZE, ttl: float = _CACHE_TTL_S) -> None:
        self._maxsize: int = maxsize
        self._ttl: float = ttl
        # OrderedDict preserves insertion order for LRU-style eviction
        self._store: collections.OrderedDict[str, float] = collections.OrderedDict()
        self._lock: threading.Lock = threading.Lock()

    def suspend(self, session_id: str) -> None:
        with self._lock:
            self._evict_expired_locked()
            if session_id in self._store:
                # Refresh TTL
                self._store.move_to_end(session_id)
                self._store[session_id] = time.monotonic()
                return
            if len(self._store) >= self._maxsize:
                # Evict the oldest entry
                _ = self._store.popitem(last=False)
            self._store[session_id] = time.monotonic()

    def is_suspended(self, session_id: str) -> bool:
        with self._lock:
            ts = self._store.get(session_id)
            if ts is None:
                return False
            if time.monotonic() - ts > self._ttl:
                del self._store[session_id]
                return False
            return True

    def unsuspend(self, session_id: str) -> None:
        with self._lock:
            _ = self._store.pop(session_id, None)

    def clear(self) -> None:
        """Drop every suspension. Test-support, and process shutdown."""
        with self._lock:
            self._store.clear()

    # The set-like spellings the previous module-global supported. Kept so the
    # existing call sites and tests read unchanged; `suspend`/`unsuspend`/
    # `is_suspended` are the port and the ones new code should use.
    def add(self, session_id: str) -> None:
        self.suspend(session_id)

    def discard(self, session_id: str) -> None:
        self.unsuspend(session_id)

    def __contains__(self, session_id: str) -> bool:
        return self.is_suspended(session_id)

    def _evict_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [k for k, ts in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]
