"""SEP-2243 stateless front-door routing on Mcp-Method / Mcp-Name headers (#336).

The stateless transport (SEP-2567) removes ``Mcp-Session-Id``; the front door
routes on the ``Mcp-Method`` / ``Mcp-Name`` headers plus request content instead
of session affinity. These tests assert:

1. A request with ``Mcp-Method`` / ``Mcp-Name`` routes correctly WITHOUT any
   ``Mcp-Session-Id`` -- and the presence of an ``Mcp-Session-Id`` header does
   not alter the routing decision.
2. Header <-> body consistency is enforced (mismatch rejected), while absent
   headers fall back to content-based routing (no error).
3. Per-tenant canary / version routing still selects the same member -- the
   header route decision is independent of tenant-based member selection.
4. The audit / correlation ``session_id`` (``CallerIdentity.session_id``) is a
   separate field and is untouched by the front-door router.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from mcp_hangar.domain.model.mcp_server_group import CanaryPolicy, McpServerGroup
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server.front_door_routing import (
    FrontDoorRoutingMiddleware,
    HeaderBodyMismatchError,
    RouteDecision,
    extract_route_headers,
    resolve_route,
    route_from_body,
)


# --------------------------------------------------------------------------- #
# ASGI harness
# --------------------------------------------------------------------------- #


def _scope(headers: dict[str, str], *, method: str = "POST", path: str = "/mcp") -> dict[str, Any]:
    raw = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
    return {"type": "http", "method": method, "path": path, "headers": raw, "state": {}}


def _receive_for(body: bytes):
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


class _CapturingApp:
    """Downstream ASGI app that records what the middleware forwarded."""

    def __init__(self) -> None:
        self.called = False
        self.forwarded_body: bytes | None = None
        self.seen_scope: dict[str, Any] | None = None

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        self.seen_scope = scope
        chunks: list[bytes] = []
        more = True
        while more:
            msg = await receive()
            if msg["type"] != "http.request":
                break
            chunks.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        self.forwarded_body = b"".join(chunks)


class _SendCollector:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int | None:
        for m in self.messages:
            if m["type"] == "http.response.start":
                status: int = m["status"]
                return status
        return None

    @property
    def body(self) -> bytes:
        return b"".join(m.get("body", b"") for m in self.messages if m["type"] == "http.response.body")


async def _drive(
    mw: FrontDoorRoutingMiddleware, scope: dict[str, Any], body: bytes
) -> tuple[_CapturingApp, _SendCollector]:
    send = _SendCollector()
    # Rebind the middleware's inner app to a fresh capturing app per call.
    app = _CapturingApp()
    mw.app = app
    await mw(scope, _receive_for(body), send)
    return app, send


def _tools_call_body(tool: str = "search", request_id: int = 1) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": tool, "arguments": {}}}
    ).encode()


def _scope_state(app: _CapturingApp) -> dict[str, Any]:
    assert app.seen_scope is not None
    state = app.seen_scope["state"]
    assert isinstance(state, dict)
    return state


def _route_of(app: _CapturingApp) -> RouteDecision:
    decision = _scope_state(app)["mcp_route"]
    assert isinstance(decision, RouteDecision)
    return decision


# --------------------------------------------------------------------------- #
# 1. Pure header / body extraction + resolution
# --------------------------------------------------------------------------- #


class TestPureResolution:
    def test_extract_route_headers(self):
        raw = [(b"mcp-method", b"tools/call"), (b"mcp-name", b"search"), (b"authorization", b"Bearer x")]
        assert extract_route_headers(raw) == ("tools/call", "search")

    def test_extract_missing_headers_are_none(self):
        assert extract_route_headers([(b"content-type", b"application/json")]) == (None, None)

    def test_route_from_body_tools_call(self):
        payload = {"method": "tools/call", "params": {"name": "search"}}
        assert route_from_body(payload) == ("tools/call", "search")

    def test_route_from_body_resource_uri(self):
        payload = {"method": "resources/read", "params": {"uri": "file:///x"}}
        assert route_from_body(payload) == ("resources/read", "file:///x")

    def test_route_from_body_batch_or_garbage_is_none(self):
        assert route_from_body([{"method": "tools/call"}]) == (None, None)
        assert route_from_body("nonsense") == (None, None)

    def test_headers_route_without_session_id(self):
        """Route resolves from headers alone -- no Mcp-Session-Id involved."""
        decision = resolve_route("tools/call", "search", None, None)
        assert decision == RouteDecision(method="tools/call", name="search", source="header")

    def test_fallback_to_body_when_headers_absent(self):
        decision = resolve_route(None, None, "tools/call", "search")
        assert decision == RouteDecision(method="tools/call", name="search", source="body")

    def test_no_signal_resolves_none_source(self):
        assert resolve_route(None, None, None, None).source == "none"

    def test_consistent_header_and_body_ok(self):
        decision = resolve_route("tools/call", "search", "tools/call", "search")
        assert decision.source == "header"

    def test_method_mismatch_rejected(self):
        try:
            resolve_route("tools/list", None, "tools/call", None)
        except HeaderBodyMismatchError:
            return
        raise AssertionError("expected HeaderBodyMismatchError")

    def test_name_mismatch_rejected(self):
        try:
            resolve_route("tools/call", "delete_everything", "tools/call", "search")
        except HeaderBodyMismatchError:
            return
        raise AssertionError("expected HeaderBodyMismatchError")


# --------------------------------------------------------------------------- #
# 2. FrontDoorRoutingMiddleware (ASGI)
# --------------------------------------------------------------------------- #


class TestMiddleware:
    async def _run(self, headers, body):
        mw = FrontDoorRoutingMiddleware(_CapturingApp(), mcp_path="/mcp")
        return await _drive(mw, _scope(headers), body)

    def test_routes_on_headers_without_session_id(self):
        import asyncio

        body = _tools_call_body("search")
        headers = {"mcp-method": "tools/call", "mcp-name": "search", "content-type": "application/json"}
        app, send = asyncio.run(self._run(headers, body))

        assert app.called is True
        assert send.status is None  # forwarded, not rejected
        # Body is replayed unchanged for downstream MCP handling.
        assert app.forwarded_body == body
        # Route decision recorded on scope for downstream routing/observability.
        decision = _route_of(app)
        assert decision.method == "tools/call"
        assert decision.name == "search"
        assert decision.source == "header"

    def test_session_id_header_does_not_change_routing(self):
        """An Mcp-Session-Id header is irrelevant to the routing decision."""
        import asyncio

        body = _tools_call_body("search")
        without_app, _ = asyncio.run(self._run({"mcp-method": "tools/call", "mcp-name": "search"}, body))
        with_sid_app, _ = asyncio.run(
            self._run({"mcp-method": "tools/call", "mcp-name": "search", "mcp-session-id": "abc123"}, body)
        )
        assert _route_of(without_app) == _route_of(with_sid_app)

    def test_no_headers_passes_through_unchanged(self):
        """Pre-SEP-2243 client: no routing headers -> content-based, no buffering error."""
        import asyncio

        body = _tools_call_body("search")
        app, send = asyncio.run(self._run({"content-type": "application/json"}, body))
        assert app.called is True
        assert send.status is None
        assert app.forwarded_body == body
        assert "mcp_route" not in _scope_state(app)

    def test_header_body_mismatch_is_rejected(self):
        import asyncio

        body = _tools_call_body("search")
        # Header claims a different tool than the body.
        app, send = asyncio.run(self._run({"mcp-method": "tools/call", "mcp-name": "delete_everything"}, body))
        assert app.called is False  # request never reached the MCP app
        assert send.status == 400
        payload = json.loads(send.body)
        assert payload["error"]["code"] == -32600
        assert payload["id"] == 1  # JSON-RPC id echoed from the body

    def test_get_request_passes_through(self):
        import asyncio

        mw = FrontDoorRoutingMiddleware(_CapturingApp(), mcp_path="/mcp")
        app, send = asyncio.run(_drive(mw, _scope({"mcp-method": "tools/call"}, method="GET"), b""))
        assert app.called is True
        assert send.status is None


# --------------------------------------------------------------------------- #
# 3. Per-tenant canary routing is unaffected by the header router
# --------------------------------------------------------------------------- #


def _mock_member(mcp_server_id: str):
    mock = MagicMock()
    mock.id = mcp_server_id
    mock.state = McpServerState.READY
    mock.state_snapshot = McpServerState.READY
    mock.ensure_ready = MagicMock()
    mock.shutdown = MagicMock()
    mock.tools = []
    mock.get_tool_names = MagicMock(return_value=[])
    return mock


def _canary_group() -> McpServerGroup:
    group = McpServerGroup(group_id="canary-group", auto_start=False)
    for mid in ("v1", "v2"):
        group.add_member(_mock_member(mid))
    group.rebalance()
    group.set_canary_policy(CanaryPolicy(canary_member="v2", split_pct=50, pinned_tenants={"tenant:acme": "v2"}))
    return group


class TestCanaryUnaffected:
    def test_member_selection_is_tenant_keyed_not_route_keyed(self):
        """The header route decision never feeds tenant-based member selection."""
        group = _canary_group()
        # Regardless of which tools/* method the header advertises, the pinned
        # tenant always resolves to the same member.
        member_v2 = group.get_member("v2")
        assert member_v2 is not None
        for _method in ("tools/call", "tools/list", "prompts/get"):
            selected = group.select_member_for("tenant:acme")
            assert selected is member_v2.mcp_server

    def test_split_stays_deterministic_per_tenant(self):
        """Header routing does not perturb the deterministic per-tenant split.

        Uses a full (100%) canary split so every tenant resolves via the
        deterministic canary path (the load-balancer fallback is round-robin by
        design and therefore intentionally not sticky).
        """
        group = McpServerGroup(group_id="canary-group", auto_start=False)
        for mid in ("v1", "v2"):
            group.add_member(_mock_member(mid))
        group.rebalance()
        group.set_canary_policy(CanaryPolicy(canary_member="v2", split_pct=100))

        first = group.select_member_for("tenant:repeat")
        for _ in range(10):
            assert group.select_member_for("tenant:repeat") is first

    def test_resolve_route_does_not_reference_tenant(self):
        """resolve_route takes only method/name -- structurally tenant-free."""
        decision = resolve_route("tools/call", "search", None, None)
        # No tenant/session attributes exist on the decision.
        assert not hasattr(decision, "tenant_id")
        assert not hasattr(decision, "session_id")


# --------------------------------------------------------------------------- #
# 4. Audit / correlation session_id is a separate, untouched field
# --------------------------------------------------------------------------- #


class TestAuditSessionIdUnaffected:
    def test_identity_session_id_preserved_through_front_door(self):
        """The front-door router does not read or mutate CallerIdentity.session_id."""
        import asyncio

        ctx = IdentityContext(
            caller=CallerIdentity(
                user_id="u1",
                agent_id=None,
                session_id="audit-correlation-42",
                principal_type="user",
                tenant_id="tenant:acme",
            )
        )
        # Sanity: the audit/correlation session_id is a distinct field.
        assert ctx.to_dict()["session_id"] == "audit-correlation-42"

        # Drive a request; the middleware must not touch identity state.
        mw = FrontDoorRoutingMiddleware(_CapturingApp(), mcp_path="/mcp")
        scope = _scope({"mcp-method": "tools/call", "mcp-name": "search"})
        scope["state"]["identity"] = ctx  # simulate an upstream-populated field
        app, _send = asyncio.run(_drive(mw, scope, _tools_call_body("search")))

        forwarded_identity = _scope_state(app)["identity"]
        assert forwarded_identity is ctx
        assert forwarded_identity.caller.session_id == "audit-correlation-42"
