"""SEP-2243 stateless front-door routing on ``Mcp-Method`` / ``Mcp-Name``.

The 2026-07-28 MCP transport removes sessions (SEP-2567), so there is no
``Mcp-Session-Id`` to anchor routing. Under SEP-2243 a stateless front door
routes on the ``Mcp-Method`` / ``Mcp-Name`` HTTP headers plus request content
instead of session affinity.

Security caveat (spec): ``Mcp-Method`` / ``Mcp-Name`` derive from client/LLM
controlled arguments and MUST NOT be trusted for security or authorization
decisions. This module uses them for routing / observability ONLY. Identity,
tenant, and per-tenant canary/version routing stay on the authenticated request
(``get_identity_context().caller.tenant_id``) and are untouched here. The
audit/correlation ``session_id`` (``CallerIdentity.session_id``, consumed by the
compliance exporters) is a SEPARATE field and is likewise untouched.

To keep the front door honest, when both a header and the request body carry the
method/name we require them to agree; a mismatch is rejected. When the headers
are absent we fall back to content-based routing (no error), preserving
backward-compatible behavior for pre-SEP-2243 clients.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from typing import Any, Literal

from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

#: Lower-cased ASGI header names (ASGI delivers header names lower-cased).
MCP_METHOD_HEADER = b"mcp-method"
MCP_NAME_HEADER = b"mcp-name"

#: JSON-RPC params keys that carry the routable target name, in priority order.
_NAME_PARAM_KEYS = ("name", "uri")

RouteSource = Literal["header", "body", "none"]


class HeaderBodyMismatchError(ValueError):
    """Raised when ``Mcp-Method`` / ``Mcp-Name`` disagree with the request body."""


@dataclass(frozen=True)
class RouteDecision:
    """The front-door routing decision for a single request.

    ``method`` / ``name`` are for routing and observability only; they are never
    used for authorization or tenant selection.
    """

    method: str | None
    name: str | None
    source: RouteSource


def extract_route_headers(raw_headers: Iterable[tuple[bytes, bytes]]) -> tuple[str | None, str | None]:
    """Return ``(mcp_method, mcp_name)`` decoded from ASGI ``raw_headers``.

    Missing headers yield ``None``. Values are latin-1 decoded and stripped;
    empty values are treated as absent.
    """
    method: str | None = None
    name: str | None = None
    for key, value in raw_headers:
        lowered = key.lower()
        if lowered == MCP_METHOD_HEADER:
            method = value.decode("latin-1").strip() or None
        elif lowered == MCP_NAME_HEADER:
            name = value.decode("latin-1").strip() or None
    return method, name


def route_from_body(payload: Any) -> tuple[str | None, str | None]:
    """Extract ``(method, name)`` from a parsed JSON-RPC request ``payload``.

    Returns ``(None, None)`` for anything that is not a single JSON-RPC object
    (e.g. a batch list or a malformed body); such requests skip
    header/body consistency checks and fall through to content-based routing.
    """
    if not isinstance(payload, dict):
        return None, None
    method = payload.get("method")
    method_str = method.strip() if isinstance(method, str) and method.strip() else None

    name_str: str | None = None
    params = payload.get("params")
    if isinstance(params, dict):
        for key in _NAME_PARAM_KEYS:
            candidate = params.get(key)
            if isinstance(candidate, str) and candidate.strip():
                name_str = candidate.strip()
                break
    return method_str, name_str


def resolve_route(
    header_method: str | None,
    header_name: str | None,
    body_method: str | None,
    body_name: str | None,
) -> RouteDecision:
    """Resolve the routing method/name, preferring headers and validating them.

    - Headers win when present (SEP-2243 stateless routing).
    - When a header and the body both carry a value, they MUST agree; otherwise
      :class:`HeaderBodyMismatchError` is raised (fail-closed at the front door).
    - When the headers are absent, fall back to the body (content-based routing).

    Raises:
        HeaderBodyMismatchError: if ``Mcp-Method`` or ``Mcp-Name`` contradicts
            the request body.
    """
    if header_method is not None and body_method is not None and header_method != body_method:
        raise HeaderBodyMismatchError(f"Mcp-Method header '{header_method}' does not match body method '{body_method}'")
    if header_name is not None and body_name is not None and header_name != body_name:
        raise HeaderBodyMismatchError(f"Mcp-Name header '{header_name}' does not match body name '{body_name}'")

    method = header_method if header_method is not None else body_method
    name = header_name if header_name is not None else body_name
    if header_method is not None or header_name is not None:
        source: RouteSource = "header"
    elif body_method is not None or body_name is not None:
        source = "body"
    else:
        source = "none"
    return RouteDecision(method=method, name=name, source=source)


class FrontDoorRoutingMiddleware:
    """ASGI middleware that routes the stateless front door on SEP-2243 headers.

    The middleware only engages for HTTP ``POST`` requests to the MCP path that
    carry an ``Mcp-Method`` or ``Mcp-Name`` header. For those it buffers and
    parses the JSON body, enforces header/body consistency (rejecting mismatches
    with a JSON-RPC error), records the :class:`RouteDecision` on the ASGI scope
    state for downstream routing/observability, and replays the body unchanged.

    Every other request -- GET/SSE, non-MCP paths, and pre-SEP-2243 clients that
    send no routing headers -- passes through untouched, so there is no behavior
    change and no body buffering for legacy traffic. Identity, tenant, canary
    routing, and the audit ``session_id`` are never read or modified here.
    """

    def __init__(self, app: Any, mcp_path: str = "/mcp") -> None:
        self.app = app
        self._mcp_path = mcp_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._should_engage(scope):
            await self.app(scope, receive, send)
            return

        header_method, header_name = extract_route_headers(scope.get("headers", []))
        if header_method is None and header_name is None:
            # Pre-SEP-2243 client: no routing headers -> content-based routing.
            await self.app(scope, receive, send)
            return

        body = await _buffer_body(receive)
        try:
            payload = json.loads(body) if body else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        body_method, body_name = route_from_body(payload)

        try:
            decision = resolve_route(header_method, header_name, body_method, body_name)
        except HeaderBodyMismatchError as exc:
            await _reject(scope, send, str(exc), payload)
            return

        _stash_route(scope, decision)
        await self.app(scope, _replay(body), send)

    def _should_engage(self, scope: Scope) -> bool:
        if scope.get("type") != "http" or scope.get("method", "").upper() != "POST":
            return False
        path = scope.get("path", "")
        return bool(path == self._mcp_path or path.startswith(self._mcp_path.rstrip("/") + "/"))


def _stash_route(scope: Scope, decision: RouteDecision) -> None:
    """Record the route decision on the ASGI scope for downstream use.

    Preserves the existing scope ``state`` object (Starlette ``State`` or a
    dict); ``state.auth`` and any identity fields are left untouched.
    """
    state = scope.get("state")
    if isinstance(state, dict):
        state["mcp_route"] = decision
    elif state is not None:
        # Starlette State (or any attribute-bag) -- set without clobbering peers.
        state.mcp_route = decision
    else:
        scope["mcp_route"] = decision


async def _buffer_body(receive: Receive) -> bytes:
    """Consume the full HTTP request body from ``receive``."""
    chunks: list[bytes] = []
    more_body = True
    while more_body:
        message = await receive()
        if message["type"] != "http.request":
            continue
        chunks.append(message.get("body", b""))
        more_body = message.get("more_body", False)
    return b"".join(chunks)


def _replay(body: bytes) -> Receive:
    """Return a ``receive`` callable that yields the buffered ``body`` once."""
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


async def _reject(scope: Scope, send: Send, message: str, payload: Any) -> None:
    """Send a JSON-RPC-shaped 400 error for a header/body mismatch."""
    request_id = payload.get("id") if isinstance(payload, dict) else None
    response = JSONResponse(
        status_code=400,
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32600, "message": message},
        },
    )
    await response(scope, _replay(b""), send)


__all__ = [
    "MCP_METHOD_HEADER",
    "MCP_NAME_HEADER",
    "RouteDecision",
    "HeaderBodyMismatchError",
    "extract_route_headers",
    "route_from_body",
    "resolve_route",
    "FrontDoorRoutingMiddleware",
]
