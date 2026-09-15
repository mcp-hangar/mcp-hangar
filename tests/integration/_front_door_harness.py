"""A front door served the way ``serve --http`` serves it, over one real upstream.

Shared by the tests that must go over the real streamable-HTTP transport rather
than drive the handlers with a stand-in request context (#1368, #1369). On this
front door the identity is re-bound per request, in a task the ASGI wrapper does
not own, and the SDK lists tools by itself before it dispatches a call (#1049);
a stand-in context has hidden fail-opens in exactly those places before.

* The composition ``serve --http`` serves,
  ``mcp_app_for_serving(build_serving_mcp_server())``, behind the authentication
  layer it mounts (``create_auth_enforced_app``). Each tenant authenticates with
  its own API key.
* ``GET /metrics`` answered by the endpoint ``serve --http`` mounts
  (`lifecycle.metrics_endpoint`), routed ahead of the MCP app as the served
  process routes it.
* One real upstream: an in-process HTTP MCP server behind a real ``McpServer``.
  It is started through the command bus, and its catalogue lands in the
  projection registry through the ``McpServerStarted`` handler bootstrap
  subscribes.

Naming: neutral placeholders only (store, read_item, write_item, tenant:a, tenant:b).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
from starlette.testclient import TestClient

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.fastmcp_server import catalogue_warmup

# The SDK's DNS-rebinding protection wants a loopback Host with a port.
BASE_URL = "http://127.0.0.1:8000"
MODERN = "2026-07-28"
LEGACY = "2025-06-18"
_ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "front-door-probe", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

SERVER = "store"
TENANT_A = "tenant:a"
TENANT_B = "tenant:b"
METHOD_NOT_FOUND = -32601


class Upstream(BaseHTTPRequestHandler):
    """A minimal JSON-answering MCP upstream that records the tools it is asked to call."""

    tools: ClassVar[tuple[str, ...]] = ()
    called: ClassVar[list[str]] = []
    #: A tool named here is answered only once its event is set, so a test can
    #: keep a call in flight. The call is recorded in `called` when it arrives.
    holds: ClassVar[dict[str, threading.Event]] = {}

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if "id" not in request:  # a notification
            self._send(202, b"")
            return
        method = request.get("method")
        params = request.get("params") or {}
        if method == "tools/call":
            self.called.append(params.get("name"))
            hold = self.holds.get(params.get("name"))
            if hold is not None:
                hold.wait(timeout=30)
        definitions = [
            {"name": name, "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}}}
            for name in self.tools
        ]
        answer = {
            "initialize": {
                "result": {
                    "protocolVersion": LEGACY,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "upstream", "version": "0"},
                }
            },
            "tools/list": {"result": {"tools": definitions}},
            "tools/call": {"result": {"content": [{"type": "text", "text": f"did {params.get('name')}"}]}},
        }.get(method, {"error": {"code": METHOD_NOT_FOUND, "message": f"Unknown method: {method}"}})
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def jsonrpc(response: httpx.Response) -> dict[str, Any]:
    """The JSON-RPC payload, from either framing (plain JSON or an SSE frame).

    The status is not asserted here. The modern entry answers a JSON-RPC error
    with the HTTP status the SDK maps its code to (404 for ``-32601``), and the
    handshake era answers 200 on the SSE framing. Where the status is part of
    what a caller could tell apart, the test compares it.
    """
    text = response.text.lstrip()
    if text.startswith("{"):
        return dict(json.loads(text))
    for line in text.splitlines():
        if line.startswith("data: "):
            return dict(json.loads(line[len("data: ") :]))
    raise AssertionError(f"neither JSON nor an SSE data frame: {text[:200]!r}")


@dataclass
class FrontDoor:
    client: TestClient
    keys: dict[str, str]
    upstream: type[Upstream]

    def post(
        self, tenant: str, method: str, params: dict[str, Any], *, era: str = MODERN, request_id: int = 1
    ) -> httpx.Response:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": era,
            "X-API-Key": self.keys[tenant],
        }
        body_params = dict(params)
        if era == MODERN:
            # Self-describing and SEP-2243 routed: the modern era has no handshake.
            headers["Mcp-Method"] = method
            if method == "tools/call":
                headers["Mcp-Name"] = params["name"]
            body_params["_meta"] = _ENVELOPE
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body_params}
        return self.client.post("/mcp", headers=headers, content=json.dumps(body))

    def tools(self, tenant: str, *, era: str = MODERN) -> list[dict[str, Any]]:
        """``tools/list`` as the client receives it: every definition, on the wire."""
        return list(jsonrpc(self.post(tenant, "tools/list", {}, era=era))["result"]["tools"])

    def names(self, tenant: str, *, era: str = MODERN) -> list[str]:
        """The names in ``tools/list`` as the client receives it."""
        return sorted(tool["name"] for tool in self.tools(tenant, era=era))

    def call(
        self,
        tenant: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        era: str = MODERN,
        request_id: int = 1,
    ) -> httpx.Response:
        params = {"name": name, "arguments": arguments or {}}
        return self.post(tenant, "tools/call", params, era=era, request_id=request_id)

    def error(self, tenant: str, name: str, *, era: str = MODERN) -> dict[str, Any]:
        payload = jsonrpc(self.call(tenant, name, era=era))
        assert "error" in payload, payload
        return dict(payload["error"])

    def result(self, tenant: str, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = jsonrpc(self.call(tenant, name, arguments))
        assert "result" in payload, payload
        assert not payload["result"].get("isError"), payload
        return dict(payload["result"])

    def scrape(self) -> str:
        """``GET /metrics``, unauthenticated, as Prometheus scrapes it."""
        response = self.client.get("/metrics")
        assert response.status_code == 200, response.text[:200]
        assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
        return response.text


def _runtime(endpoint: str, also: tuple[str, ...] = ()) -> Any:
    """The fleet: one remote upstream, the two commands the invoke path sends, the projection handler.

    Each id in *also* is one more server on the same upstream, registered cold.
    """
    from mcp_hangar.application.commands import InvokeToolCommand, StartMcpServerCommand
    from mcp_hangar.application.commands.handlers import InvokeToolHandler, StartMcpServerHandler
    from mcp_hangar.application.event_handlers.tool_projection_handler import ToolProjectionPopulationHandler
    from mcp_hangar.bootstrap.runtime import create_runtime
    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import McpServerStarted
    from mcp_hangar.domain.model import McpServer
    from mcp_hangar.infrastructure.command_bus import CommandBus
    from mcp_hangar.server.context import init_context

    bus = CommandBus()
    runtime = create_runtime(command_bus=bus)
    bus.register(StartMcpServerCommand, StartMcpServerHandler(runtime.repository, runtime.event_bus))
    bus.register(InvokeToolCommand, InvokeToolHandler(runtime.repository, runtime.event_bus))
    # Subscribed the way bootstrap subscribes it (`server/bootstrap/event_handlers.py`).
    projection = ToolProjectionPopulationHandler(repository=runtime.repository)
    runtime.event_bus.subscribe(McpServerStarted, projection.handle, kind=HandlerKind.LOCAL_VIEW)
    runtime.repository.add(SERVER, McpServer(mcp_server_id=SERVER, mode="remote", endpoint=endpoint))
    for server_id in also:
        runtime.repository.add(server_id, McpServer(mcp_server_id=server_id, mode="remote", endpoint=endpoint))
    init_context(runtime)
    bus.send(StartMcpServerCommand(mcp_server_id=SERVER))
    return runtime


def _with_metrics(served: Any) -> Any:
    """*served* with ``GET /metrics`` routed ahead of it, as ``serve --http`` routes it."""
    from starlette.applications import Starlette
    from starlette.routing import Route

    from mcp_hangar.server.lifecycle import metrics_endpoint

    scrape = Starlette(routes=[Route("/metrics", metrics_endpoint, methods=["GET"])])

    async def app(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("path") == "/metrics":
            await scrape(scope, receive, send)
            return
        await served(scope, receive, send)

    return app


@contextmanager
def front_door(
    tools: tuple[str, ...],
    policies: dict[str, tuple[str, ...]] | None = None,
    *,
    topology: str = "front_door",
    also: tuple[str, ...] = (),
) -> Iterator[FrontDoor]:
    """A served front door over one upstream exposing *tools*, each tenant with an API key.

    *policies* maps a tenant to its allow-list on the upstream; a tenant without
    one is allowed everything. *topology* serves the same gateway in the
    default ``egress`` instead, where the upstream is reached through
    ``hangar_call``, for a test that compares the two surfaces. Each id in
    *also* is one more server on the same upstream, registered cold.
    """
    from mcp_hangar.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator, InMemoryApiKeyStore
    from mcp_hangar.auth.infrastructure.middleware import AuthenticationMiddleware
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.bootstrap import build_serving_mcp_server
    from mcp_hangar.server.context import reset_context
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    reset_tool_projection_registry()
    reset_tool_access_resolver()
    catalogue_warmup.reset()
    resolver = get_tool_access_resolver()
    resolver.set_topology_mode(topology)
    for tenant, allowed in (policies or {}).items():
        resolver.set_standalone_member_policy(SERVER, tenant, ToolAccessPolicy(allow_list=allowed))

    handler: type[Upstream] = type("_ThisUpstream", (Upstream,), {"tools": tools, "called": [], "holds": {}})
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    runtime = None
    try:
        runtime = _runtime(f"http://127.0.0.1:{upstream.server_address[1]}/mcp", also)
        discovered = sorted(projection.tool for projection in get_tool_projection_registry().all())
        assert discovered == sorted(tools), f"the upstream's catalogue did not reach the registry: {discovered}"

        store = InMemoryApiKeyStore()
        keys = {
            tenant: store.create_key(principal_id=f"agent-{label}", name=f"key-{label}", tenant_id=tenant)
            for label, tenant in (("a", TENANT_A), ("b", TENANT_B))
        }
        auth = SimpleNamespace(authn_middleware=AuthenticationMiddleware([ApiKeyAuthenticator(store)]))
        app = _with_metrics(create_auth_enforced_app(mcp_app_for_serving(build_serving_mcp_server()), auth))
        with TestClient(app, base_url=BASE_URL) as client:
            yield FrontDoor(client, keys, handler)
    finally:
        if runtime is not None:
            for server in runtime.repository.get_all().values():
                server.shutdown()
        upstream.shutdown()
        upstream.server_close()
        reset_context()
        reset_tool_projection_registry()
        reset_tool_access_resolver()
        catalogue_warmup.reset()
