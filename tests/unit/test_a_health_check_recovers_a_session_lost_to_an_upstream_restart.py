"""A health check recovers a session the upstream lost in a restart (#1699).

A call re-handshakes once when the upstream no longer knows its session (#651).
The health check and the catalogue probes called the client directly, so after an
upstream restart they presented the dead session, failed, and counted against the
server until three failures degraded it and a restart reconnected it -- minutes
after the upstream was back.

This runs a real stateful Streamable-HTTP-style upstream on a loopback port: it
issues `Mcp-Session-Id` on `initialize`, answers 404 to a request carrying an
unknown one and 400 to a request with none, and "restarts" by forgetting every
session. The server under test is a real remote `McpServer` on the real
`HttpClient`.
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.security.ssrf import set_trusted_networks, trusted_networks


class _Upstream:
    def __init__(self) -> None:
        self.sessions: set[str] = set()
        self.initializes = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a) -> None:
                pass

            def _send(self, status: int, body: dict | None, headers: dict | None = None) -> None:
                payload = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self) -> None:  # noqa: N802 -- http.server's name
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                method, rid = request.get("method"), request.get("id")
                if method == "initialize":
                    owner.initializes += 1
                    session = uuid.uuid4().hex
                    owner.sessions.add(session)
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "u", "version": "1"},
                    }
                    return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": result}, {"Mcp-Session-Id": session})
                if method and method.startswith("notifications/"):
                    return self._send(202, None)
                session = self.headers.get("Mcp-Session-Id")
                if session is None:
                    return self._send(
                        400, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "missing session"}}
                    )
                if session not in owner.sessions:
                    return self._send(
                        404, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32001, "message": "no session"}}
                    )
                if method == "tools/list":
                    tools = [{"name": "echo", "description": "", "inputSchema": {"type": "object"}}]
                    return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
                return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def restart(self) -> None:
        """A restarted upstream knows no session it issued."""
        self.sessions.clear()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def upstream():
    server = _Upstream()
    saved = trusted_networks()
    set_trusted_networks([])
    yield server
    set_trusted_networks(saved)
    server.close()


@pytest.fixture
def server(upstream):
    s = McpServer(mcp_server_id="restarting", mode="remote", endpoint=upstream.url, enforce_ssrf=False)
    s.ensure_ready()
    yield s
    s.shutdown()


def test_a_health_check_after_an_upstream_restart_succeeds_and_counts_nothing(upstream, server):
    assert server.health_check() is True
    upstream.restart()
    before = server.health.consecutive_failures

    assert server.health_check() is True

    assert server.health.consecutive_failures == before == 0
    assert upstream.initializes == 2, "the session was not renegotiated"


def test_the_next_probe_after_that_still_succeeds(upstream, server):
    upstream.restart()
    assert server.health_check() is True
    assert server.health_check() is True
    assert upstream.initializes == 2


def test_a_catalogue_refresh_after_an_upstream_restart_succeeds(upstream, server):
    upstream.restart()

    assert server._refresh_tools() is True
