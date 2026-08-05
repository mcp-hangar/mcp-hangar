"""Session suspension endpoints.

The registry itself is an infrastructure adapter -- see
`infrastructure/session_suspension.py`. This module is routes.
"""

from __future__ import annotations

import json
import re
from typing import cast

from starlette.requests import Request
from starlette.routing import Route

from ...infrastructure.session_suspension import InMemorySessionSuspensionRegistry
from ...logging_config import get_logger
from .serializers import HangarJSONResponse

logger = get_logger(__name__)

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")

# The process-wide registry. It stays a module global because the routes are
# plain functions with no injection point; the enforcement handler no longer
# reaches for it -- it is handed the same object at bootstrap.
_suspended_sessions = InMemorySessionSuspensionRegistry()


def get_session_suspension_registry() -> InMemorySessionSuspensionRegistry:
    """The registry these routes read and write.

    Exists so bootstrap can hand the SAME instance to the enforcement handler.
    A second instance would mean a session suspended by a detection rule stayed
    servable, and one suspended over HTTP invisible to enforcement -- the two
    would silently disagree.
    """
    return _suspended_sessions


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
