"""A handshake-era ``notifications/cancelled`` over streamable HTTP reaches the call it names (#2289).

Since #877 the handshake era is served stateless: each POST gets a transport
and a server of its own, which are gone once it is answered. A client that
gives up on a request POSTs ``notifications/cancelled`` as a request of its own,
and the server built for that POST has no request with that id, so the SDK
dropped it (``no handler for notification notifications/cancelled``). The
original POST's stream stays open, and on this era that is correct: a
disconnect is not a cancel. A call held for approval went on being held, and an
approval made after the client had been told the call failed still ran it.

This wrap reads such a POST before the SDK answers it, and when an
authenticated principal sent it, it cancels the calls held for the named
request (`ApprovalGateService.cancel_client_request`). The approval record is
where it finds them, so it works on any replica: the holder reads the record
and does not run the call, and a resolver anywhere is refused.

What it does not do:

* act for a caller with no principal, or the anonymous one: a JSON-RPC id is
  the client's own choice, so it names nothing without a caller to scope it;
* act on a 2026-07-28 request: that era has no client-to-server notifications
  over HTTP, the client closes the request's stream instead, and the SDK's
  modern entry cancels the handler then;
* read a body larger than a cancel can be, or one without a length.

The POST itself still goes to the SDK, which answers it as before.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from starlette.types import Receive, Scope, Send

from mcp_hangar._sdk_compat import is_modern_protocol_version

from ..logging_config import get_logger
from .front_door_routing import _buffer_body, _protocol_version, _replay

logger = get_logger(__name__)

#: A cancel is a small object; a body larger than this is not read here.
MAX_CANCEL_BODY_BYTES = 4096


def serve_client_cancels(app: Any, *, mcp_path: str = "/mcp") -> Any:
    """Wrap *app* so a handshake-era ``notifications/cancelled`` cancels the calls held for its request."""

    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        if not _may_be_a_cancel(scope, mcp_path):
            await app(scope, receive, send)
            return
        body = await _buffer_body(receive)
        if body is None:
            return
        request_id = cancelled_request_id(body)
        if request_id is not None:
            await _cancel_held_calls(scope, request_id)
        await app(scope, _replay(body, receive), send)

    return wrapped


def _may_be_a_cancel(scope: Scope, mcp_path: str) -> bool:
    if scope.get("type") != "http" or scope.get("method", "").upper() != "POST":
        return False
    path = scope.get("path", "")
    if path != mcp_path and not path.startswith(mcp_path.rstrip("/") + "/"):
        return False
    headers = scope.get("headers", [])
    if is_modern_protocol_version(_protocol_version(headers)):
        return False
    length = next((value for key, value in headers if key.lower() == b"content-length"), None)
    try:
        return length is not None and 0 < int(length) <= MAX_CANCEL_BODY_BYTES
    except ValueError:
        return False


def cancelled_request_id(body: bytes) -> str | None:
    """The request a ``notifications/cancelled`` body names, as JSON text; None for any other body."""
    try:
        message = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(message, dict) or message.get("method") != "notifications/cancelled" or "id" in message:
        return None
    params = message.get("params")
    request_id = params.get("requestId") if isinstance(params, dict) else None
    if isinstance(request_id, bool) or not isinstance(request_id, int | str):
        return None
    return json.dumps(request_id)


async def _cancel_held_calls(scope: Scope, request_id: str) -> None:
    try:
        from starlette.requests import Request

        from ..server.context import get_context
        from .asgi import identity_for_request

        request = Request(scope)
        principal = getattr(getattr(request.state, "auth", None), "principal", None)
        if principal is None or principal.is_anonymous():
            return
        identity = identity_for_request(SimpleNamespace(request=request))
        gate = getattr(get_context(), "approval_gate", None)
        if identity is None or gate is None:
            return
        caller = identity.caller
        await gate.cancel_client_request(caller.user_id or caller.agent_id, caller.tenant_id, request_id)
    except Exception:  # noqa: BLE001 -- fault-barrier: a cancel that cannot be applied leaves the hold as it was
        logger.warning("client_cancel_not_applied", exc_info=True)
