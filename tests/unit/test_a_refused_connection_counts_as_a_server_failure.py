"""A refused connection on a call, a relay or a refresh is a failure of the server (#1725).

The HTTP client raises the domain `ClientError` for a refused connection. These
paths caught only `(OSError, TimeoutError)`, so it escaped before `record_failure`:
the server's own failure count never moved (the group still counted it), and the
relay surfaced a raw `ClientError` instead of `ToolInvocationError`.

A real remote `McpServer` on the real `HttpClient` against a loopback upstream
that is then closed, so the next connection is really refused.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mcp_hangar.domain.exceptions import ToolInvocationError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.security.ssrf import set_trusted_networks, trusted_networks
from mcp_hangar.domain.value_objects import McpServerState


class _Upstream:
    def __init__(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802 -- http.server's name
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                method, rid = request.get("method"), request.get("id")
                if method and method.startswith("notifications/"):
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "u", "version": "1"},
                    }
                elif method == "tools/list":
                    result = {"tools": [{"name": "echo", "description": "", "inputSchema": {"type": "object"}}]}
                else:
                    result = {"content": []}
                body = json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    upstream = _Upstream()
    saved = trusted_networks()
    set_trusted_networks([])
    s = McpServer(mcp_server_id="gone", mode="remote", endpoint=upstream.url, max_consecutive_failures=10)
    s.ensure_ready()
    upstream.close()  # from here on the connection is refused
    yield s
    s.shutdown()
    set_trusted_networks(saved)


def test_a_refused_tool_call_moves_the_servers_failure_count(server):
    before = server.health.consecutive_failures

    with pytest.raises(ToolInvocationError):
        server.invoke_tool("echo", {})

    assert server.health.consecutive_failures == before + 1


def test_the_relay_raises_tool_invocation_error_not_a_raw_client_error(server):
    with pytest.raises(ToolInvocationError):
        server.relay_request("tasks/get", {"taskId": "t"})


def test_a_refused_catalogue_refresh_is_a_quiet_false(server):
    assert server._refresh_tools() is False


def test_a_refused_catalogue_recheck_is_a_quiet_false(server):
    assert server.recheck_catalogue() is False


def test_an_oversized_response_keeps_its_type_and_does_not_count_against_the_server():
    """`ResponseTooLarge` is a `ClientError` too, and a limit, not a fault (it keeps escaping as itself)."""
    from unittest.mock import MagicMock

    from mcp_hangar.domain.exceptions import ResponseTooLarge

    s = McpServer(mcp_server_id="big", mode="remote", endpoint="http://127.0.0.1:9/mcp")
    s._state = McpServerState.READY
    s._client = MagicMock()
    s._client.is_alive.return_value = True
    s._client.call.side_effect = ResponseTooLarge(10, mcp_server_id="big")

    with pytest.raises(ResponseTooLarge):
        s.relay_request("tasks/get", {"taskId": "t"})
