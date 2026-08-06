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

from ...domain.events import DomainEvent, SessionSuspended, SessionUnsuspended
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


def _announce(event: DomainEvent) -> None:
    """Tell the other replicas about a suspension decision taken here.

    The decision is already applied locally by the caller, so this is what makes
    it fleet-wide rather than what makes it happen. Failing to announce is
    therefore a degradation and not an error -- these routes are reachable in
    configurations with no runtime assembled -- but it is a loud one, because
    the difference between "blocked" and "blocked on one pod out of three" is
    exactly what an operator needs to know.
    """
    try:
        from ..state import get_runtime

        get_runtime().event_bus.publish(event)
    except Exception as e:  # noqa: BLE001 -- boundary: no bus is a shape, not a fault
        logger.warning(
            "session_suspension_not_announced",
            error=str(e),
            detail="applied on this instance only; other replicas will not enforce it",
        )


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

    # Applied here *and* announced. Announcing alone would be tidier, and it
    # fails silently if the projection is not subscribed -- an operator would
    # get a 200 and no block anywhere. Applied first, the block always holds on
    # this replica; the event carries it to the others.
    _suspended_sessions.add(session_id)
    _announce(SessionSuspended(session_id=session_id, reason=reason or "", source="api"))

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
    _announce(SessionUnsuspended(session_id=session_id, source="api"))
    logger.info("session_unsuspended", session_id=session_id)
    return HangarJSONResponse({"session_id": session_id, "suspended": False})


session_routes = [
    Route("/{session_id:str}/suspend", suspend_session, methods=["POST"]),
    Route("/{session_id:str}/suspend", unsuspend_session, methods=["DELETE"]),
]
