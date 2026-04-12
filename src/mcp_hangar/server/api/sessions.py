"""Session suspension endpoint and in-process registry."""

# pyright: reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false

from __future__ import annotations

import json
import threading
from typing import cast

from starlette.requests import Request
from starlette.routing import Route

from ...logging_config import get_logger
from .serializers import HangarJSONResponse

logger = get_logger(__name__)

_suspended_sessions: set[str] = set()
_sessions_lock = threading.Lock()


def is_session_suspended(session_id: str) -> bool:
    """Return whether a session is currently suspended."""
    with _sessions_lock:
        return session_id in _suspended_sessions


async def suspend_session(request: Request) -> HangarJSONResponse:
    """Suspend a session in the local in-memory registry."""
    session_id = cast(str, request.path_params["session_id"])
    reason: str | None = None

    try:
        body = await request.json()
        if isinstance(body, dict):
            raw_reason = body.get("reason")
            if isinstance(raw_reason, str):
                reason = raw_reason
    except (json.JSONDecodeError, ValueError):
        pass

    with _sessions_lock:
        _suspended_sessions.add(session_id)

    logger.info("session_suspended", session_id=session_id, reason=reason)
    return HangarJSONResponse({"session_id": session_id, "suspended": True})


session_routes = [
    Route("/{session_id:str}/suspend", suspend_session, methods=["POST"]),
]
