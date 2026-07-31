"""The modern surface is served by the app ``serve --http`` actually builds (#560).

These probes go through ``build_serving_mcp_server()`` -- the function
``bootstrap()`` itself calls -- plus ``streamable_http_app()``, which is what
``ServerLifecycle.run_http`` serves. Deliberately NOT through
``MCPServerFactory``: the factory surface was already unit-tested and green while
the shipped CLI surface 404'd, because the factory has no production call site,
so asserting it again would reproduce that blind spot. ``bootstrap()`` itself is
not callable from tests (it registers process-global command handlers); that it
calls this builder is pinned separately in ``test_bootstrap.py``.

Covered:
- ``GET``/``POST /server/discover`` reachable (SEP-2575 discovery entry point).
- a stateless 2026-07-28 ``tools/list`` + ``tools/call`` POST to ``/mcp`` is
  served next to the legacy ``initialize`` handshake on the same endpoint.
- ``serverInfo.name`` agrees between ``initialize`` and ``server/discover``.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from mcp_hangar.fastmcp_server.config import HANGAR_SERVER_NAME

# The SDK auto-enables DNS-rebinding protection for loopback binds, and its
# allowed-host patterns carry a port -- so the probe must present a Host header
# that matches one ("testserver" gets a 421).
_BASE_URL = "http://127.0.0.1:8000"

_MODERN_VERSION = "2026-07-28"
_LEGACY_VERSION = "2025-06-18"

#: Reserved ``params._meta`` envelope a self-describing 2026-07-28 request must
#: carry now that there is no ``initialize`` handshake to negotiate them.
_ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": _MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "compat-probe", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _modern_headers(method: str, name: str | None = None) -> dict[str, str]:
    """Headers for a modern stateless POST: era + SEP-2243 routing + both Accepts.

    Both ``application/json`` and ``text/event-stream`` are required: the server
    runs in SSE-response mode, and a json-only ``Accept`` is answered 406 (see
    ``test_modern_post_requires_sse_accept``).
    """
    headers = {
        "MCP-Protocol-Version": _MODERN_VERSION,
        "Mcp-Method": method,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


def _modern_body(method: str, params: dict | None = None) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {**(params or {}), "_meta": _ENVELOPE}})


def _jsonrpc(text: str) -> dict:
    """Return the JSON-RPC payload from either response framing.

    The eras frame differently on the same endpoint: a modern stateless result
    comes back as plain ``application/json``, while the legacy handshake answers
    on the SSE stream (``event: message`` + ``data:``).
    """
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    raise AssertionError(f"neither JSON nor an SSE data frame: {text[:200]!r}")


@pytest.fixture(scope="module")
def client():
    """Probe client over the ASGI app ``serve --http`` builds.

    Mirrors ``run_http``'s composition, front-door wrap included -- not just the
    bare ``streamable_http_app()``. That wrap is load-bearing here: it buffers and
    replays request bodies, and a replayed stream reports a disconnect once
    drained, which the modern SDK entry reads as "client gone" and cancels on. A
    fixture over the unwrapped app passes while the served app 500s.

    Module-scoped because the SDK's ``StreamableHTTPSessionManager.run()`` -- the
    app's lifespan -- may be entered only once per instance. Every probe below is
    read-only.
    """
    from mcp_hangar.fastmcp_server.modern_surface import wrap_front_door_routing
    from mcp_hangar.server.bootstrap import build_serving_mcp_server

    app = wrap_front_door_routing(build_serving_mcp_server().streamable_http_app())
    with TestClient(app, base_url=_BASE_URL) as test_client:
        yield test_client


class TestServerDiscoverIsServed:
    """SEP-2575 ``server/discover`` must be reachable on the shipped surface."""

    def test_get_returns_discover_result(self, client):
        response = client.get("/server/discover")

        assert response.status_code == 200
        body = response.json()
        assert _MODERN_VERSION in body["supportedVersions"]
        assert body["serverInfo"]["name"] == HANGAR_SERVER_NAME
        assert "tools" in body

    def test_post_returns_jsonrpc_envelope(self, client):
        response = client.post(
            "/server/discover",
            json={"jsonrpc": "2.0", "id": 42, "method": "server/discover", "params": {}},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == 42
        assert body["result"]["serverInfo"]["name"] == HANGAR_SERVER_NAME


class TestModernInvokePathIsServed:
    """A stateless 2026-07-28 POST to ``/mcp`` is served without a handshake."""

    def test_tools_list(self, client):
        response = client.post("/mcp", headers=_modern_headers("tools/list"), content=_modern_body("tools/list"))

        assert response.status_code == 200, response.text
        assert "tools" in _jsonrpc(response.text)["result"]

    def test_tools_call(self, client):
        response = client.post(
            "/mcp",
            headers=_modern_headers("tools/call", name="hangar_health"),
            content=_modern_body("tools/call", {"name": "hangar_health", "arguments": {}}),
        )

        assert response.status_code == 200, response.text
        assert "content" in _jsonrpc(response.text)["result"]

    def test_modern_post_requires_sse_accept(self, client):
        """A json-only ``Accept`` is 406 -- the requirement clients must satisfy.

        Pinned because it is the failure the compat harness originally recorded
        as "the modern surface is not served"; it is a client-side Accept
        requirement, not a missing route.
        """
        headers = {**_modern_headers("tools/list"), "Accept": "application/json"}

        response = client.post("/mcp", headers=headers, content=_modern_body("tools/list"))

        assert response.status_code == 406

    def test_sep2243_header_body_mismatch_is_rejected(self, client):
        """``Mcp-Name`` contradicting the body's ``name`` fails closed (SDK-enforced)."""
        response = client.post(
            "/mcp",
            headers=_modern_headers("tools/call", name="hangar_health"),
            content=_modern_body("tools/call", {"name": "hangar_list", "arguments": {}}),
        )

        assert response.status_code == 400

    def test_sep2243_mismatch_rejected_in_the_handshake_era_too(self, client):
        """The other enforcement owner: our front-door wrap covers legacy-era POSTs.

        Same fail-closed outcome, different code path -- the SDK's modern entry
        does not see this request, so a missing front-door wrap would let the
        contradicting header through.
        """
        response = client.post(
            "/mcp",
            headers={
                "MCP-Protocol-Version": _LEGACY_VERSION,
                "Mcp-Method": "tools/call",
                "Mcp-Name": "hangar_health",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "hangar_list"}}),
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32600


class TestOneServerIdentity:
    """``initialize`` and ``server/discover`` must not report different servers."""

    def test_initialize_and_discover_agree(self, client):
        from mcp_hangar import __version__

        initialize = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
            content=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": _LEGACY_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "compat-probe", "version": "0"},
                    },
                }
            ),
        )
        assert initialize.status_code == 200, initialize.text
        handshake_info = _jsonrpc(initialize.text)["result"]["serverInfo"]

        discover_info = client.get("/server/discover").json()["serverInfo"]

        assert handshake_info["name"] == discover_info["name"] == HANGAR_SERVER_NAME
        # Version too: unset, the SDK reports ITS version here, so the two
        # surfaces disagreed on which software the client was talking to.
        assert handshake_info["version"] == discover_info["version"] == __version__


class TestTopologyIsHonouredOnTheServedApp:
    """`tool_access.mode: front_door` must actually change the served surface (#596).

    It did not: the gate lived only in `MCPServerFactory`, which nothing calls,
    so a gateway configured front_door kept serving the `hangar_*` meta-API —
    lifecycle control included — to the callers front_door exists to fail closed
    on. Driven over the real app rather than by inspecting handlers, because the
    handler wiring is exactly what was wrong.
    """

    @staticmethod
    def _tools_over(client) -> list[str]:
        response = client.post(
            "/mcp",
            headers=_modern_headers("tools/list"),
            content=_modern_body("tools/list"),
        )
        assert response.status_code == 200, response.text
        return [tool["name"] for tool in _jsonrpc(response.text)["result"]["tools"]]

    def test_egress_serves_the_meta_api(self, client):
        """The default topology is untouched."""
        names = self._tools_over(client)

        assert any(name.startswith("hangar_") for name in names), names

    def test_front_door_withholds_the_meta_api_from_an_unidentified_caller(self):
        from mcp_hangar.domain.services.tool_access_resolver import (
            get_tool_access_resolver,
            reset_tool_access_resolver,
        )
        from mcp_hangar.fastmcp_server.modern_surface import wrap_front_door_routing
        from mcp_hangar.server.bootstrap import build_serving_mcp_server

        reset_tool_access_resolver()
        get_tool_access_resolver().set_topology_mode("front_door")
        try:
            app = wrap_front_door_routing(build_serving_mcp_server().streamable_http_app())
            with TestClient(app, base_url=_BASE_URL) as front_door_client:
                names = self._tools_over(front_door_client)
        finally:
            reset_tool_access_resolver()

        leaked = [name for name in names if name.startswith("hangar_")]
        assert not leaked, f"front_door still serves the meta-API: {leaked[:6]}"
