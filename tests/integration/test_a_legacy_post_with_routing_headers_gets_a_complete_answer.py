"""A handshake-era ``POST /mcp`` carrying ``Mcp-Method`` / ``Mcp-Name`` is answered in full (#2108).

The SEP-2243 wrap buffers the body of such a POST and replays it to the SDK.
The replay used to report ``http.disconnect`` as soon as the body was read, and
the handshake-era SSE response, which listens for exactly that, stopped before
writing a byte: the client got ``200`` and an empty stream, uvicorn logged
"ASGI callable returned without completing response". That hit every legacy POST
the wrap engaged on, a single matching header included, not only a duplicated one.

A repeated ``Mcp-Method`` or ``Mcp-Name`` is now refused with the 400 /
``HEADER_MISMATCH`` the 2026-07-28 entry gives it, since a first-copy and a
last-copy reader would route the request differently.

A ``200`` alone proves nothing here -- the broken case was a ``200`` too -- so
every served case parses the SSE frame and checks the result.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from mcp_hangar.tasks_wire import HEADER_MISMATCH

# The SDK's DNS-rebinding protection wants a Host it allows, port included.
_BASE_URL = "http://127.0.0.1:8000"
_LEGACY_VERSION = "2025-06-18"
_BASE_HEADERS = [
    ("MCP-Protocol-Version", _LEGACY_VERSION),
    ("Accept", "application/json, text/event-stream"),
    ("Content-Type", "application/json"),
]


def _body(method: str, params: dict | None = None) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}})


def _sse_result(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    raise AssertionError(f"no SSE data frame in the answer: {text[:200]!r}")


@pytest.fixture(scope="module")
def client():
    """The app ``serve --http`` builds: front-door wrap over the stateless SDK app.

    Module-scoped because the SDK session manager's lifespan may run only once
    per app instance.
    """
    from mcp_hangar.fastmcp_server.modern_surface import wrap_front_door_routing
    from mcp_hangar.server.bootstrap import build_serving_mcp_server

    app = wrap_front_door_routing(build_serving_mcp_server().streamable_http_app(stateless_http=True))
    with TestClient(app, base_url=_BASE_URL) as test_client:
        yield test_client


class TestServedPostsCarryTheirResult:
    def test_a_single_mcp_method_header_is_served(self, client):
        response = client.post(
            "/mcp", headers=[*_BASE_HEADERS, ("Mcp-Method", "tools/list")], content=_body("tools/list")
        )

        assert response.status_code == 200
        assert "tools" in _sse_result(response.text)["result"]

    def test_matching_method_and_name_headers_are_served(self, client):
        response = client.post(
            "/mcp",
            headers=[*_BASE_HEADERS, ("Mcp-Method", "tools/call"), ("Mcp-Name", "hangar_health")],
            content=_body("tools/call", {"name": "hangar_health", "arguments": {}}),
        )

        assert response.status_code == 200
        assert "content" in _sse_result(response.text)["result"]

    def test_a_post_without_routing_headers_is_served_as_before(self, client):
        response = client.post("/mcp", headers=_BASE_HEADERS, content=_body("tools/list"))

        assert response.status_code == 200
        assert "tools" in _sse_result(response.text)["result"]


class TestARepeatedRoutingHeaderIsRefused:
    @pytest.mark.parametrize(
        ("header", "routing", "method", "params"),
        [
            ("Mcp-Method", [("Mcp-Method", "tools/list")] * 2, "tools/list", {}),
            (
                "Mcp-Name",
                [("Mcp-Method", "tools/call"), *[("Mcp-Name", "hangar_health")] * 2],
                "tools/call",
                {"name": "hangar_health", "arguments": {}},
            ),
        ],
    )
    def test_the_refusal_matches_the_modern_entry(self, client, header, routing, method, params):
        headers = [*_BASE_HEADERS, *routing]

        response = client.post("/mcp", headers=headers, content=_body(method, params))

        assert response.status_code == 400
        assert response.json() == {
            "jsonrpc": "2.0",
            "id": 7,
            "error": {"code": HEADER_MISMATCH, "message": f"{header.lower()} header appears more than once"},
        }

    def test_a_mismatch_is_still_refused_as_a_mismatch(self, client):
        response = client.post(
            "/mcp",
            headers=[*_BASE_HEADERS, ("Mcp-Method", "tools/call"), ("Mcp-Name", "hangar_health")],
            content=_body("tools/call", {"name": "hangar_list"}),
        )

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == HEADER_MISMATCH
        assert "does not match" in error["message"]
