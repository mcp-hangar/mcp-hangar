"""The sessionless handshake-era ``GET /mcp`` stream: the HTTP channel for ``list_changed`` (#1366, part B).

Since #877 the handshake era is served stateless, so a POST is answered and
gone and nothing is left to push on. This stream is the back-channel. A client
opens it after ``notifications/initialized``, with no session id, and it
carries exactly one kind of message: ``notifications/tools/list_changed``, sent
once as it opens and then whenever this caller's tenant projection changes (see
:mod:`tool_list_changed` for the comparison and the coalescing). POSTs stay
stateless; this adds no session.

Who opens it: the TypeScript SDK client opens it after ``initialized`` whether
or not it holds a session id, and treats a 405 as "no stream offered". The
Python SDK 2.0.0 client opens it only when it holds a session id, so over HTTP
it gets no push and relies on #1231's wait.

What it checks, in order, before anything is held:

* the method is served at all: where no push channel is advertised (egress, or
  no publisher), the answer is 405, as ``listChanged: false`` already says.
  The SDK used to hold an empty stream open here for as long as the client
  stayed;
* the SDK's DNS-rebinding guard, with the settings the POST path uses;
* ``Accept`` names ``text/event-stream``;
* the caller's session is not suspended (``session_guard``), again before every
  frame, so a suspension ends an open stream;
* the per-principal, per-tenant and global caps
  (:data:`tool_list_changed.MAX_STREAMS_PER_PRINCIPAL`,
  :data:`tool_list_changed.MAX_STREAMS_PER_TENANT`,
  :data:`tool_list_changed.MAX_CHANNELS`): 429, 429 and 503.

Authentication is the auth layer's, which wraps ``/mcp`` for every method; the
principal it leaves on the request is the one a POST is served as, and the
tenant it names scopes what the stream is told. It runs once, as the stream
opens, so the stream ends when that could have changed: when the principal's
API key or a role is revoked (:func:`subscribe_revocations`, on every replica
that tails the event), when a JWT's ``exp`` passes, and after
:data:`MAX_LIFETIME_S` at the latest. The reconnect then authenticates again.
The TypeScript client reconnects a GET stream that the server closes, and is
told once on open.

Tenant-less callers (``allow_anonymous``, or auth off on loopback) share one
principal and so one per-principal cap between them.

Per replica (#877): a stream is told about the catalogue of the replica that
holds it. A client whose stream lands on another replica after a reconnect is
told once on open, and re-lists.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from sse_starlette.sse import EventSourceResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import metrics as prometheus_metrics
from ..logging_config import get_logger
from . import tool_list_changed

if TYPE_CHECKING:
    from ..server.session_guard import SessionSuspendedError

logger = get_logger(__name__)

#: A comment frame this often keeps a proxy with an idle timeout from cutting the stream.
KEEPALIVE_S = 15  # an int: sse-starlette types its ping interval so

#: A stream ends after this long, so its caller authenticates again on the reconnect.
MAX_LIFETIME_S = 3600.0

#: A client that stops reading is dropped after a write waits this long.
SEND_TIMEOUT_S = 30.0

#: Notifications waiting for the socket. They are all the same message, so a full queue loses nothing.
_QUEUE_SIZE = 4

_REFUSED = prometheus_metrics.TOOL_LIST_CHANGED_STREAMS_REFUSED_TOTAL
_ENDED = prometheus_metrics.TOOL_LIST_CHANGED_STREAMS_ENDED_TOTAL

_NOTIFICATION = json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})


def serve_tool_list_changed_stream(app: ASGIApp, *, mcp_path: str = "/mcp") -> ASGIApp:
    """Answer a handshake-era ``GET`` on *mcp_path* here; pass everything else to *app*."""
    from .._sdk_compat import HANDSHAKE_PROTOCOL_VERSIONS

    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("method") == "GET" and scope.get("path") == mcp_path:
            version = _header(scope, b"mcp-protocol-version")
            if version is None or version in HANDSHAKE_PROTOCOL_VERSIONS:
                await _serve(scope, receive, send)
                return
        await app(scope, receive, send)

    return wrapped


def _header(scope: Scope, name: bytes) -> str | None:
    return next((value.decode("latin-1") for key, value in scope.get("headers", ()) if key.lower() == name), None)


async def _respond(send: Send, status: int, body: bytes = b"", headers: tuple[tuple[bytes, bytes], ...] = ()) -> None:
    await send({"type": "http.response.start", "status": status, "headers": [*headers]})
    await send({"type": "http.response.body", "body": body})


def _refusal(reason: str, message: str) -> bytes:
    from ..server.session_guard import SESSION_SUSPENDED_CODE

    error = {"code": SESSION_SUSPENDED_CODE, "message": message, "data": {"reason": reason}}
    return json.dumps({"jsonrpc": "2.0", "id": None, "error": error}).encode()


def _suspended(request_ctx: Any) -> SessionSuspendedError | None:
    """The suspension refusal for this caller, or None. Fail-closed, like every chokepoint."""
    from ..server.session_guard import SessionSuspendedError, refuse_if_session_suspended

    try:
        refuse_if_session_suspended("tool_list_changed", request_ctx)
    except SessionSuspendedError as exc:
        return exc
    return None


async def _serve(scope: Scope, receive: Receive, send: Send) -> None:
    from mcp.server.transport_security import TransportSecurityMiddleware
    from starlette.requests import Request

    from .asgi import identity_for_request, mcp_transport_security

    if not tool_list_changed.advertises_tools_list_changed():
        _REFUSED.inc(reason="not_served")
        await _respond(send, 405, headers=((b"allow", b"POST"),))
        return

    request = Request(scope, receive)
    rejected = await TransportSecurityMiddleware(mcp_transport_security()).validate_request(request, is_post=False)
    if rejected is not None:
        await rejected(scope, receive, send)
        return
    if "text/event-stream" not in (request.headers.get("accept") or ""):
        await _respond(send, 406, b"Not Acceptable: Client must accept text/event-stream")
        return

    request_ctx = SimpleNamespace(request=request)
    refused = _suspended(request_ctx)
    if refused is not None:
        _REFUSED.inc(reason="session_suspended")
        await _respond(send, 403, _refusal(refused.reason, str(refused)), ((b"content-type", b"application/json"),))
        return

    identity = identity_for_request(request_ctx)
    caller = identity.caller if identity is not None else None
    tenant_id = caller.tenant_id if caller is not None else None
    principal_id = caller.user_id if caller is not None else None
    # repr: a tenant named "None" is not the tenant-less callers' bucket.
    owner = f"{tenant_id!r}\x00{principal_id or 'anonymous'}"

    # True: tell the client. False: end the stream (a revocation named its principal).
    queue: asyncio.Queue[bool] = asyncio.Queue()

    async def notify() -> None:
        # All the same message, so a backlog past a few loses nothing.
        if queue.qsize() < _QUEUE_SIZE:
            queue.put_nowait(True)

    key = id(queue)
    full = tool_list_changed.open_stream(
        key, tenant_id, owner, notify, principal=principal_id, end=lambda: queue.put_nowait(False)
    )
    if full is not None:
        # Debug: a client retrying at its cap would otherwise fill the log. The counter is the signal.
        logger.debug("tool_list_changed_stream_refused", reason=full)
        _REFUSED.inc(reason=full)
        status = 429 if full in ("principal_cap", "tenant_cap") else 503
        await _respond(send, status, headers=((b"retry-after", b"60"),))
        return

    try:
        await notify()  # whatever landed between the client's listing and now
        prometheus_metrics.TOOL_LIST_CHANGED_NOTIFICATIONS_TOTAL.inc(transport="http")
        # sse-starlette, as the SDK's own GET stream uses: it pings, it ends the
        # stream when the client leaves, and it ends it when the server is told
        # to stop, which a hand-rolled stream did not and so held shutdown open.
        response = EventSourceResponse(
            _frames(queue, request_ctx, _credential_expires_at(request)),
            ping=KEEPALIVE_S,
            send_timeout=SEND_TIMEOUT_S,
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )
        await response(scope, receive, send)
    finally:
        tool_list_changed.close_stream(key)


def _credential_expires_at(request: Any) -> float | None:
    """When the credential this stream opened with stops being valid (epoch seconds), if it says.

    The verified expiry of a JWT or API key is carried on the principal. For a
    rotated API key, this is the earlier of its expiry and rotation grace end.
    Revocation also ends the stream (:func:`subscribe_revocations`).
    """
    principal = getattr(getattr(getattr(request, "state", None), "auth", None), "principal", None)
    expires_at = (getattr(principal, "metadata", None) or {}).get("expires_at")
    return float(expires_at) if isinstance(expires_at, int | float) and not isinstance(expires_at, bool) else None


async def _frames(
    queue: asyncio.Queue[bool], request_ctx: Any, expires_at: float | None
) -> AsyncIterator[dict[str, str]]:
    """One ``list_changed`` per notification, until the stream has to end.

    It ends when the caller's session is suspended (checked before each frame
    and at least every :data:`KEEPALIVE_S`, so an idle stream too), when its
    principal's key or role is revoked, when its credential expires, and after
    :data:`MAX_LIFETIME_S` at the latest.
    """
    loop = asyncio.get_running_loop()
    ends, why = loop.time() + MAX_LIFETIME_S, "lifetime"
    if expires_at is not None and (left := expires_at - time.time()) < MAX_LIFETIME_S:
        ends, why = loop.time() + left, "credential_expired"
    while (left := ends - loop.time()) > 0:
        try:
            tell = await asyncio.wait_for(queue.get(), timeout=min(KEEPALIVE_S, left))
        except TimeoutError:
            tell = None
        if tell is False:
            _ENDED.inc(reason="credential_revoked")
            return
        if _suspended(request_ctx) is not None:
            _ENDED.inc(reason="session_suspended")
            return
        if tell:
            yield {"event": "message", "data": _NOTIFICATION}
    _ENDED.inc(reason=why)


def subscribe_revocations(event_bus: Any) -> None:
    """End a principal's streams when its API key or a role is revoked.

    ``PROJECTION``: it acts on the event's own payload (the principal id), on
    every replica, because a stream on any replica may belong to that
    principal; ending an ended stream again does nothing, and it publishes
    nothing. A role matters because the management tools a caller is projected
    follow its role (#904), so the reconnect's listing is the one to trust.
    """
    from ..domain.contracts.event_bus import HandlerKind
    from ..domain.events import ApiKeyRevoked, RoleRevoked

    def end(event: Any) -> None:
        tool_list_changed.end_streams(str(event.principal_id))

    event_bus.subscribe(ApiKeyRevoked, end, kind=HandlerKind.PROJECTION)
    event_bus.subscribe(RoleRevoked, end, kind=HandlerKind.PROJECTION)
