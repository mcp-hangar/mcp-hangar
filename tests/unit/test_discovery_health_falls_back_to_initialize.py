"""A discovered server without a health endpoint passes if it answers `initialize` (#1756).

Discovery quarantined every HTTP server whose `/health` was not 200, and many
MCP servers serve only their MCP path: core's own `task_upstream` example, and
so every container-mode server the Kubernetes operator runs
(mcp-hangar-operator#306). A 404 on the health path now falls back to an MCP
`initialize` on the server's MCP path. A 5xx or a timeout is not second-guessed.

The servers here are real HTTP servers on loopback, reached through the
validator's own aiohttp client.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

# The validator skips the health check without aiohttp. The dev extra installs
# it, so this skips only on an install without it, never in CI.
pytest.importorskip("aiohttp")

from mcp_hangar.application.discovery.security_validator import (
    SecurityConfig,
    SecurityValidator,
    ValidationResult,
)
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer

INITIALIZE_RESULT = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "u", "version": "0"}}


def _handler(health_status: int, mcp: str) -> type[BaseHTTPRequestHandler]:
    """`mcp` is how the MCP path answers: json, sse, error, or 404."""

    class Handler(BaseHTTPRequestHandler):
        posted: list[dict[str, Any]] = []

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802 -- http.server's handler name
            self.send_response(health_status if self.path == "/health" else 404)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            type(self).posted.append({"path": self.path, "request": request, "accept": self.headers.get("Accept")})
            if self.path != "/mcp" or mcp == "404":
                self.send_response(404)
                self.end_headers()
                return
            if mcp == "error":
                body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32600, "message": "no"}})
                content_type = "application/json"
            elif mcp == "sse":
                message = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": INITIALIZE_RESULT})
                body = f"event: message\ndata: {message}\n\n"
                content_type = "text/event-stream"
            else:
                body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": INITIALIZE_RESULT})
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())

    return Handler


@pytest.fixture
def serve() -> Iterator[Any]:
    servers: list[ThreadingHTTPServer] = []

    def start(health_status: int, mcp: str) -> tuple[DiscoveredMcpServer, type[BaseHTTPRequestHandler]]:
        handler = _handler(health_status, mcp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        discovered = DiscoveredMcpServer.create(
            name="boxed",
            source_type="kubernetes",
            mode="http",
            connection_info={
                "host": "127.0.0.1",
                "port": server.server_address[1],
                "path": "/mcp",
                "health_path": "/health",
            },
        )
        return discovered, handler

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


async def _validate(discovered: DiscoveredMcpServer) -> Any:
    return await SecurityValidator(SecurityConfig(require_health_check=True))._validate_health(discovered)


@pytest.mark.parametrize("mcp", ["json", "sse"])
async def test_no_health_endpoint_passes_on_initialize(serve, mcp):
    discovered, handler = serve(404, mcp)

    assert await _validate(discovered) is None
    [post] = handler.posted
    assert post["path"] == "/mcp"
    assert post["request"]["method"] == "initialize"
    assert "text/event-stream" in post["accept"]


async def test_a_healthy_health_endpoint_needs_no_initialize(serve):
    discovered, handler = serve(200, "404")

    assert await _validate(discovered) is None
    assert handler.posted == []


async def test_a_server_error_on_health_is_not_second_guessed(serve):
    discovered, handler = serve(500, "json")

    report = await _validate(discovered)

    assert report is not None and report.result is ValidationResult.FAILED_HEALTH
    assert "status 500" in report.reason
    assert handler.posted == [], "a 5xx is a real failure; no fallback"


@pytest.mark.parametrize("mcp", ["404", "error"])
async def test_no_health_and_no_initialize_result_fails_naming_both(serve, mcp):
    discovered, _ = serve(404, mcp)

    report = await _validate(discovered)

    assert report is not None and report.result is ValidationResult.FAILED_HEALTH
    assert "404" in report.reason and "initialize" in report.reason, report.reason
