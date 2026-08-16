"""One client, two replicas, one request each way (#877).

Every session test in this tree drives a single app instance, which is exactly
the shape that cannot see this defect: a handshake-era session lives in ONE
process's `StreamableHTTPSessionManager._server_instances`, so a suite that never
builds a second instance asserts that the owning replica answers its own
sessions -- which was never in doubt. Three replicas behind a Service were three
servers to a client and nothing here noticed until it was measured on a cluster.

Two `build_serving_mcp_server().streamable_http_app()` instances are two
replicas. Sending the second request to the other one is what a Service without
session affinity does on roughly every other request.

Mirrors `test_serve_modern_surface.py`'s composition deliberately: the shipped
builder plus `streamable_http_app()` plus the front-door wrap, not
`MCPServerFactory`, which has no production call site.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

# The SDK auto-enables DNS-rebinding protection for loopback binds and its
# allowed-host patterns carry a port, so the probe must present a matching Host.
_BASE_URL = "http://127.0.0.1:8000"

#: Any revision that still negotiates through `initialize`. The modern era
#: (2026-07-28) has no session to lose -- SEP-2567 removed them -- so it cannot
#: exercise this at all.
_HANDSHAKE_VERSION = "2025-06-18"

_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": _HANDSHAKE_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "two-replica-probe", "version": "0"},
    },
}


def _jsonrpc(text: str) -> dict:
    """Return the JSON-RPC payload from either framing (plain JSON or an SSE frame)."""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    raise AssertionError(f"neither JSON nor an SSE data frame: {text[:200]!r}")


def _replica() -> TestClient:
    from mcp_hangar.fastmcp_server.modern_surface import wrap_front_door_routing
    from mcp_hangar.server.bootstrap import build_serving_mcp_server
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    return TestClient(wrap_front_door_routing(mcp_app_for_serving(build_serving_mcp_server())), base_url=_BASE_URL)


@pytest.fixture(scope="module")
def replicas():
    """Two instances of the app `serve --http` builds, entered once each.

    Module-scoped because `StreamableHTTPSessionManager.run()` -- the app's
    lifespan -- may be entered only once per instance.
    """
    with _replica() as first, _replica() as second:
        yield first, second


@pytest.fixture(scope="module")
def handshake(replicas):
    """`initialize` against the first replica; the headers a client would then carry."""
    first, _ = replicas
    response = first.post("/mcp", headers=_HEADERS, content=json.dumps(_INITIALIZE))
    assert response.status_code == 200, response.text

    headers = dict(_HEADERS, **{"MCP-Protocol-Version": _HANDSHAKE_VERSION})
    session_id = response.headers.get("mcp-session-id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _post(client: TestClient, headers: dict, request_id: int, method: str, params: dict | None = None) -> dict:
    body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    response = client.post("/mcp", headers=headers, content=json.dumps(body))
    assert response.status_code == 200, f"{method} -> {response.status_code}: {response.text[:200]}"
    return _jsonrpc(response.text)


class TestTheOtherReplicaAnswers:
    def test_tools_list_on_the_replica_that_did_not_handshake(self, replicas, handshake) -> None:
        # The reported failure. Before this, `Session not found` (-32600) at the
        # transport, with no path to recovery except starting the session over --
        # and a rolling restart or scale-down does the same thing to a client
        # that had been pinned.
        _, other = replicas

        result = _post(other, handshake, 2, "tools/list")["result"]

        assert result["tools"], "the replica that did not handshake served no tools"

    def test_both_replicas_serve_the_same_catalogue(self, replicas, handshake) -> None:
        # "One gateway" has to mean the same answer, not merely an answer. The
        # catalogue itself is made identical by every replica warming its own
        # fleet (#885); this asserts the transport does not undo that.
        first, other = replicas

        here = {tool["name"] for tool in _post(first, handshake, 3, "tools/list")["result"]["tools"]}
        there = {tool["name"] for tool in _post(other, handshake, 4, "tools/list")["result"]["tools"]}

        assert here == there

    def test_a_tool_call_lands_on_the_replica_that_did_not_handshake(self, replicas, handshake) -> None:
        # Listing could be served from a projection without a session; invoking
        # goes through the full dispatch, so it is the one that proves the
        # request was really handled rather than merely routed.
        _, other = replicas

        answer = _post(other, handshake, 5, "tools/call", {"name": "hangar_list", "arguments": {}})

        assert "result" in answer, answer


class TestNoSessionIsHandedOut:
    def test_initialize_returns_no_session_id(self, replicas) -> None:
        # The mechanism, asserted directly: there is no session to be on the
        # wrong replica of. A test that only checked the two calls above would
        # still pass if sessions came back and the Service happened to be sticky.
        first, _ = replicas

        response = first.post("/mcp", headers=_HEADERS, content=json.dumps(_INITIALIZE))

        assert response.headers.get("mcp-session-id") is None

    def test_teardown_is_refused_rather_than_faked(self, replicas, handshake) -> None:
        # The accepted cost, pinned so it is a decision and not a surprise:
        # `DELETE /mcp` answers 405, because there is no session to terminate.
        # Documented in UPGRADE.md.
        first, _ = replicas

        assert first.delete("/mcp", headers=handshake).status_code == 405
