"""Session suspension endpoint and in-process registry."""

from __future__ import annotations

import collections
import json
import re
import threading
import time
from typing import cast

from starlette.requests import Request
from starlette.routing import Route

from ...logging_config import get_logger
from .serializers import HangarJSONResponse

logger = get_logger(__name__)

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")

_CACHE_MAXSIZE = 10_000
_CACHE_TTL_S = 86_400.0  # 24 hours


class _SuspendedSessionCache:
    """Thread-safe TTL-bounded cache for suspended session IDs.

    Evicts expired entries lazily on access and proactively on add when full.
    """

    def __init__(self, maxsize: int = _CACHE_MAXSIZE, ttl: float = _CACHE_TTL_S) -> None:
        self._maxsize: int = maxsize
        self._ttl: float = ttl
        # OrderedDict preserves insertion order for LRU-style eviction
        self._store: collections.OrderedDict[str, float] = collections.OrderedDict()
        self._lock: threading.Lock = threading.Lock()

    def add(self, session_id: str) -> None:
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

    def __contains__(self, session_id: str) -> bool:
        with self._lock:
            ts = self._store.get(session_id)
            if ts is None:
                return False
            if time.monotonic() - ts > self._ttl:
                del self._store[session_id]
                return False
            return True

    def discard(self, session_id: str) -> None:
        with self._lock:
            _ = self._store.pop(session_id, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def _evict_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [k for k, ts in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]


_suspended_sessions = _SuspendedSessionCache()


def is_session_suspended(session_id: str) -> bool:
    """Return whether a session is currently suspended."""
    return session_id in _suspended_sessions


async def suspend_session(request: Request) -> HangarJSONResponse:
    """Suspend a session in the local in-memory registry."""
    session_id = cast(str, request.path_params["session_id"])

    if not _SESSION_ID_RE.match(session_id):
        return HangarJSONResponse(
            {"error": "invalid session_id: must be 1-128 alphanumeric, dash, or underscore"},
            status_code=400,
        )

    reason: str | None = None

    try:
        body: object = cast(object, json.loads((await request.body()).decode()))
        if isinstance(body, dict):
            body_dict = cast(dict[str, object], body)
            raw_reason = body_dict.get("reason")
            if isinstance(raw_reason, str):
                reason = raw_reason
    except (json.JSONDecodeError, ValueError):
        pass

    _suspended_sessions.add(session_id)

    logger.info("session_suspended", session_id=session_id, reason=reason)
    return HangarJSONResponse({"session_id": session_id, "suspended": True})


async def unsuspend_session(request: Request) -> HangarJSONResponse:
    """Remove a session from the suspended registry."""
    session_id = cast(str, request.path_params["session_id"])

    if not _SESSION_ID_RE.match(session_id):
        return HangarJSONResponse(
            {"error": "invalid session_id"},
            status_code=400,
        )

    _suspended_sessions.discard(session_id)
    logger.info("session_unsuspended", session_id=session_id)
    return HangarJSONResponse({"session_id": session_id, "suspended": False})


session_routes = [
    Route("/{session_id:str}/suspend", suspend_session, methods=["POST"]),
    Route("/{session_id:str}/suspend", unsuspend_session, methods=["DELETE"]),
]
