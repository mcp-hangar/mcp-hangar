"""Hangar speaks the 2026-07-28 stateless lifecycle to an HTTP upstream that serves it (#2009).

The start probes ``server/discover`` with the full SEP-2575 envelope. An
upstream that names 2026-07-28 is never sent ``initialize``: every POST carries
``MCP-Protocol-Version`` equal to its ``_meta`` version, plus the client info and
capabilities, and no ``Mcp-Session-Id``. Anything else falls back to the
legacy-shaped handshake, as before.

Driven through ``McpServer.ensure_ready`` with a real ``HttpClient`` whose pool
is an ``httpx.MockTransport``, so the headers and bodies asserted are the ones
that reached the transport.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from mcp_hangar.domain.exceptions import ConfigurationError, McpServerStartError
from mcp_hangar.domain.model import McpServer
from mcp_hangar.http_client import HttpClient, HttpClientConfig
from mcp_hangar.protocol import HANGAR_CLIENT_INFO
from mcp_hangar.server.config import build_config

MODERN = "2026-07-28"
VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
INFO_KEY = "io.modelcontextprotocol/clientInfo"
CAPS_KEY = "io.modelcontextprotocol/clientCapabilities"
TOOLS = [
    {
        "name": "lookup",
        "inputSchema": {"type": "object", "properties": {"region": {"type": "string", "x-mcp-header": "Region"}}},
    }
]
Answer = Callable[[dict[str, Any]], httpx.Response]


def _ok(body: dict[str, Any], result: dict[str, Any], **headers: str) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result}, headers=headers)


def _error(body: dict[str, Any], code: int, status: int = 200, data: Any = None) -> httpx.Response:
    error: dict[str, Any] = {"code": code, "message": "refused"}
    if data is not None:
        error["data"] = data
    return httpx.Response(status, json={"jsonrpc": "2.0", "id": body.get("id"), "error": error})


def _stateless(body: dict[str, Any]) -> httpx.Response:
    """A 2026-07-28 upstream: no initialize, and a session id it hands out that must not be echoed."""
    method = body["method"]
    if method == "server/discover":
        return _ok(body, {"supportedVersions": [MODERN], "capabilities": {"tools": {}}}, **{"Mcp-Session-Id": "s-1"})
    if method == "tools/list":
        return _ok(body, {"tools": TOOLS}, **{"Mcp-Session-Id": "s-1"})
    if method == "tools/call":
        return _ok(body, {"content": []})
    return _error(body, -32601)


def _legacy(discover: Callable[[dict[str, Any]], httpx.Response]) -> Answer:
    """A 2025-11-25 upstream, answering ``server/discover`` with *discover*."""

    def answer(body: dict[str, Any]) -> httpx.Response:
        method = body["method"]
        if method == "server/discover":
            return discover(body)
        if "id" not in body:
            return httpx.Response(202)
        if method == "initialize":
            return _ok(body, {"protocolVersion": "2025-11-25", "capabilities": {}}, **{"Mcp-Session-Id": "s-1"})
        return _ok(body, {"tools": TOOLS})

    return answer


def _start(answer: Answer, server: McpServer | None = None) -> tuple[McpServer, HttpClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            seen.append(request)
            return httpx.Response(405)
        seen.append(request)
        return answer(json.loads(request.content))

    client = HttpClient("http://upstream.test/mcp", http_config=HttpClientConfig(max_retries=1), mcp_server_id="up")
    defaults = client._client.headers
    client._client.close()
    client._client = httpx.Client(headers=defaults, transport=httpx.MockTransport(handle))
    server = server or McpServer(mcp_server_id="up", mode="remote", endpoint="http://upstream.test/mcp")
    with patch.object(server, "_create_client", return_value=client):
        server.ensure_ready()
    return server, client, seen


def _methods(seen: list[httpx.Request]) -> list[str]:
    return [json.loads(r.content)["method"] if r.method == "POST" else r.method for r in seen]


def _meta(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)["params"]["_meta"]


class TestAStatelessUpstream:
    def test_it_is_discovered_and_listed_without_a_handshake(self) -> None:
        _, client, seen = _start(_stateless)

        assert _methods(seen) == ["server/discover", "tools/list"]
        assert client.modern_envelope is True
        client.close()

    def test_every_post_names_its_version_in_the_header_and_the_meta(self) -> None:
        _, client, seen = _start(_stateless)
        client.call("tools/call", {"name": "lookup", "arguments": {"region": "eu"}})

        for request in seen:
            meta = _meta(request)
            assert request.headers["MCP-Protocol-Version"] == meta[VERSION_KEY] == MODERN
            assert meta[INFO_KEY] == HANGAR_CLIENT_INFO
            # Hangar declares no roots, sampling or elicitation: it relays none of them.
            assert meta[CAPS_KEY] == {}
        client.close()

    def test_no_session_id_is_kept_or_echoed(self) -> None:
        _, client, seen = _start(_stateless)
        client.call("tools/call", {"name": "lookup", "arguments": {}})

        assert all("Mcp-Session-Id" not in r.headers for r in seen)
        assert client._mcp_session_id is None
        client.close()

    def test_no_standing_get_stream_is_opened(self) -> None:
        _, client, seen = _start(_stateless)

        assert all(r.method == "POST" for r in seen)
        client.close()

    def test_a_refused_version_the_upstream_then_offers_is_retried_once(self) -> None:
        refused: list[bool] = []

        def answer(body: dict[str, Any]) -> httpx.Response:
            if not refused:
                refused.append(True)
                return _error(body, -32022, status=400, data={"supported": [MODERN], "requested": MODERN})
            return _stateless(body)

        _, client, seen = _start(answer)

        assert _methods(seen) == ["server/discover", "server/discover", "tools/list"]
        assert client.modern_envelope is True
        client.close()

    def test_an_upstream_speaking_no_version_hangar_does_fails_the_start(self) -> None:
        def answer(body: dict[str, Any]) -> httpx.Response:
            return _error(body, -32022, status=400, data={"supported": ["2099-01-01"]})

        with pytest.raises(McpServerStartError, match="no MCP protocol version"):
            _start(answer)


class TestTheMirroredHeadersStillNeedAListing:
    """``Mcp-Param-*`` comes from the schema ``tools/list`` served (#2011), judged as before (#2253)."""

    def test_the_listing_precedes_the_call_and_a_listed_tool_mirrors(self) -> None:
        _, client, seen = _start(_stateless)
        client.call("tools/call", {"name": "lookup", "arguments": {"region": "eu"}})

        methods = _methods(seen)
        assert methods.index("tools/list") < methods.index("tools/call")
        assert seen[-1].headers["Mcp-Param-Region"] == "eu"
        client.close()

    def test_a_tool_never_listed_mirrors_nothing(self) -> None:
        _, client, seen = _start(_stateless)
        client.call("tools/call", {"name": "unlisted", "arguments": {"region": "eu"}})

        assert not [name for name in seen[-1].headers if name.lower().startswith("mcp-param-")]
        client.close()


class TestALegacyUpstreamKeepsTheHandshake:
    @pytest.mark.parametrize(
        "discover",
        [
            pytest.param(lambda body: _error(body, -32601), id="method-not-found"),
            pytest.param(lambda body: httpx.Response(400, text="Bad Request: no session"), id="http-400"),
            pytest.param(lambda body: _ok(body, {}), id="result-without-versions"),
            pytest.param(lambda body: _ok(body, {"supportedVersions": ["2025-11-25"]}), id="legacy-versions"),
            pytest.param(
                lambda body: _error(body, -32022, status=400, data={"supported": ["2025-11-25"]}),
                id="handshake-era-only",
            ),
        ],
    )
    def test_it_falls_back_to_a_legacy_shaped_initialize(self, discover: Answer) -> None:
        _, client, seen = _start(_legacy(discover))

        methods = _methods(seen)
        assert methods[0] == "server/discover"
        assert methods[methods.index("initialize") :][:3] == ["initialize", "notifications/initialized", "tools/list"]
        assert client.modern_envelope is False
        for request in seen[methods.index("initialize") :]:
            if request.method != "POST":
                continue
            assert "MCP-Protocol-Version" not in request.headers
            assert VERSION_KEY not in _meta(request)
        client.close()

    def test_the_session_it_establishes_is_echoed(self) -> None:
        _, client, seen = _start(_legacy(lambda body: _error(body, -32601)))
        client.call("tools/list", {})

        assert seen[-1].headers["Mcp-Session-Id"] == "s-1"
        client.close()


class TestTheVersionRefusalReachesTheCaller:
    def _client(self, response: httpx.Response) -> HttpClient:
        client = HttpClient("http://upstream.test/mcp", http_config=HttpClientConfig(max_retries=1))
        client._client.close()
        client._client = httpx.Client(transport=httpx.MockTransport(lambda request: response))
        return client

    def test_a_400_carrying_minus_32022_is_returned_whole(self) -> None:
        error = {"code": -32022, "message": "Unsupported protocol version", "data": {"supported": [MODERN]}}
        with self._client(httpx.Response(400, json={"jsonrpc": "2.0", "id": None, "error": error})) as client:
            assert client.call("server/discover", {}) == {"error": error}

    def test_any_other_400_keeps_the_http_error_shape(self) -> None:
        error = {"code": -32602, "message": "Invalid params"}
        with self._client(httpx.Response(400, json={"jsonrpc": "2.0", "id": None, "error": error})) as client:
            answer = client.call("server/discover", {})

        assert answer["error"]["code"] == -32000
        assert answer["error"]["message"] == "HTTP error: 400"


class TestAProbeThatTimesOut:
    def test_it_reads_the_upstream_as_legacy(self) -> None:
        """As the SDK client does: a probe timeout is not an era verdict that fails the start."""

        def answer(body: dict[str, Any]) -> httpx.Response:
            if body["method"] == "server/discover":
                raise httpx.ReadTimeout("no answer to an unknown method")
            return _legacy(lambda b: _error(b, -32601))(body)

        _, client, seen = _start(answer)

        assert _methods(seen)[:3] == ["server/discover", "initialize", "notifications/initialized"]
        assert client.modern_envelope is False
        client.close()


def _loaded(**spec: Any) -> McpServer:
    """The server the config loader builds from a remote entry carrying *spec*."""
    entry = {"mode": "remote", "endpoint": "http://upstream.test/mcp", **spec}
    return build_config({"up": entry}).servers["up"]


class TestUpstreamProtocolLegacy:
    """``upstream_protocol: legacy`` keeps an upstream on the handshake era, never probing it."""

    def test_it_sends_initialize_first_and_never_discover(self) -> None:
        """Even to an upstream whose discover would have named 2026-07-28."""
        modern_discover = _legacy(lambda body: _ok(body, {"supportedVersions": [MODERN]}))
        _, client, seen = _start(modern_discover, _loaded(upstream_protocol="legacy"))

        methods = _methods(seen)
        assert methods[0] == "initialize"
        assert "server/discover" not in methods
        assert all("MCP-Protocol-Version" not in r.headers for r in seen)
        client.close()

    def test_it_keeps_the_session_the_upstream_establishes(self) -> None:
        _, client, seen = _start(_legacy(lambda body: _error(body, -32601)), _loaded(upstream_protocol="legacy"))
        client.call("tools/list", {})

        assert seen[-1].headers["Mcp-Session-Id"] == "s-1"
        client.close()

    def test_session_recovery_does_not_probe_either(self) -> None:
        lost: list[bool] = []
        legacy = _legacy(lambda body: _error(body, -32601))

        def answer(body: dict[str, Any]) -> httpx.Response:
            if body["method"] == "tools/call" and not lost:
                lost.append(True)
                return httpx.Response(404)
            if body["method"] == "tools/call":
                return _ok(body, {"content": []})
            return legacy(body)

        server, client, seen = _start(answer, _loaded(upstream_protocol="legacy"))
        result = server._call_with_session_recovery(client, "tools/call", {"name": "lookup", "arguments": {}})

        assert result["result"] == {"content": []}
        assert _methods(seen).count("initialize") == 2
        assert "server/discover" not in _methods(seen)
        client.close()


class TestUpstreamProtocolAuto:
    @pytest.mark.parametrize("spec", [{}, {"upstream_protocol": "auto"}], ids=["absent", "auto"])
    def test_it_probes_as_before(self, spec: dict[str, Any]) -> None:
        _, client, seen = _start(_stateless, _loaded(**spec))

        assert _methods(seen) == ["server/discover", "tools/list"]
        assert client.modern_envelope is True
        client.close()


class TestUpstreamProtocolIsRefused:
    @pytest.mark.parametrize("value", ["stateless", "LEGACY", "", None, True, 2026])
    def test_an_unknown_value_names_the_server_and_the_key(self, value: Any) -> None:
        with pytest.raises(ConfigurationError, match=r"mcp_servers\.up\.upstream_protocol must be one of auto, legacy"):
            _loaded(upstream_protocol=value)

    @pytest.mark.parametrize(
        "entry",
        [
            pytest.param({"mode": "subprocess", "command": ["python", "-m", "x"]}, id="stdio"),
            pytest.param({"mode": "docker", "image": "example/x:1"}, id="docker"),
            pytest.param({"mode": "container", "image": "example/x:1"}, id="container"),
        ],
    )
    def test_the_key_off_a_remote_server_is_contradictory(self, entry: dict[str, Any]) -> None:
        with pytest.raises(ConfigurationError, match=r"mcp_servers\.up\.upstream_protocol is set on a"):
            build_config({"up": {**entry, "upstream_protocol": "legacy"}})
