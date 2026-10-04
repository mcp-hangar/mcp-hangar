"""Bootstrap Hangar with digest pins, and read every tool listing over the served app (#1528).

Run as a script, in its own interpreter, by
``test_a_tools_digest_reads_on_hangar_tools_and_rest.py``:
``python _tool_digest_harness.py <out.json>``. Not collected by pytest.

A separate process because ``bootstrap()`` fills process-global state that a
second bootstrap in the same interpreter would inherit.

What runs is production: ``bootstrap()`` reading a config file with API-key
auth on, the egress topology, per-tenant and all-tenants digest pins, and a
member policy that denies ``secret_item`` to ``tenant-b``. The app is the one
``ServerLifecycle.run_http`` serves -- ``/api`` to the REST router, the rest to
the MCP app -- behind the auth enforcement it applies, under starlette's
``TestClient``. The upstream is an in-process HTTP MCP server.

The callers:

- ``tenant-a`` and ``tenant-b``: ``developer`` granted globally, each key in its
  own tenant. They read ``hangar_tools``.
- ``ops``: ``viewer`` granted globally. It reads ``GET /api/tools`` and
  ``GET /api/mcp_servers/store/tools``.
- ``scoped``: ``viewer`` granted only within ``tenant-b``. It reads the same
  two routes, which admit only a fleet-wide grant.

The report also carries ``digest_tools`` -- the function ``mcp-hangar pin``
digests a server with -- over the started server, to compare every surface to.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"

SERVER = "store"
TOOLS = ("read_item", "write_item", "secret_item", "plain_item")
TENANT_A, TENANT_B = "tenant-a", "tenant-b"
#: Pins. ``read_item``'s is deliberately not its digest, so a listing that
#: echoed the pin as the digest would be told apart from one that computed it.
PIN_READ_ALL = "e" * 64
PIN_WRITE_ALL = "b" * 64
PIN_WRITE_TENANT_A = "a" * 64
PIN_SECRET_ALL = "d" * 64
PIN_SECRET_TENANT_B = "c" * 64


class _Upstream(BaseHTTPRequestHandler):
    """An MCP upstream that lists TOOLS."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if "id" not in request:  # a notification
            self._send(202, b"")
            return
        method = request.get("method")
        answer: dict[str, Any]
        if method == "initialize":
            answer = {
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "upstream", "version": "0"},
                }
            }
        elif method == "tools/list":
            tools = [{"name": name, "description": f"{name} tool", "inputSchema": {"type": "object"}} for name in TOOLS]
            answer = {"result": {"tools": tools}}
        else:
            answer = {"error": {"code": -32601, "message": f"Unknown method: {method}"}}
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _upstream() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/mcp"


def _config(endpoint: str) -> dict[str, Any]:
    return {
        "tool_access": {"mode": "egress"},
        "rate_limit": {"rps": 1000, "burst": 1000},
        "auth": {
            "enabled": True,
            "allow_anonymous": False,
            "api_key": {"enabled": True, "header_name": "X-API-Key"},
            "storage": {"driver": "memory"},
        },
        "mcp_servers": {
            SERVER: {
                "mode": "remote",
                "endpoint": endpoint,
                "tool_access": {"member": {TENANT_B: {"deny_list": ["secret_item"]}}},
                "tool_projection": {
                    "digest_enforcement": "audit",
                    "pins": {"read_item": PIN_READ_ALL, "write_item": PIN_WRITE_ALL, "secret_item": PIN_SECRET_ALL},
                    "tenant_overrides": {
                        TENANT_A: {"pins": {"write_item": PIN_WRITE_TENANT_A}},
                        TENANT_B: {"pins": {"secret_item": PIN_SECRET_TENANT_B}},
                    },
                },
            },
        },
    }


def _served_app(context: Any) -> Any:
    """``run_http``'s app: ``/api`` to the REST API, everything else to ``/mcp``, behind its auth enforcement."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from mcp_hangar.server.api import create_api_router
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    mcp_app = mcp_app_for_serving(context.mcp_server)
    api_app = create_api_router(auth_components=getattr(context, "auth_components", None))
    aux_app = Starlette(routes=[Mount("/api", app=api_app)])

    async def combined_app(scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path", "") if scope["type"] in ("http", "websocket") else ""
        await (aux_app if path == "/api" or path.startswith("/api/") else mcp_app)(scope, receive, send)

    return create_auth_enforced_app(combined_app, context.auth_components)


def _keys(context: Any) -> dict[str, str]:
    auth = context.auth_components
    grants = {
        TENANT_A: ("developer", "global", TENANT_A),
        TENANT_B: ("developer", "global", TENANT_B),
        "ops": ("viewer", "global", None),
        "scoped": ("viewer", f"tenant:{TENANT_B}", TENANT_B),
    }
    keys = {}
    for caller, (role, scope, tenant) in grants.items():
        principal = f"svc:{caller}"
        keys[caller] = auth.api_key_store.create_key(principal_id=principal, name=caller, tenant_id=tenant)
        auth.role_store.assign_role(principal, role, scope=scope)
    return keys


def _hangar_tools(client: Any, key: str) -> dict[str, Any]:
    """One stateless ``tools/call`` of ``hangar_tools``: its parsed result and the raw body."""
    headers = {
        "MCP-Protocol-Version": MODERN_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "hangar_tools",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "X-API-Key": key,
    }
    envelope = {
        "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "tool-digest-harness", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    params = {"name": "hangar_tools", "arguments": {"mcp_server": SERVER}, "_meta": envelope}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
    response = client.post("/mcp", headers=headers, content=json.dumps(body))
    text = response.text.lstrip()
    if not text.startswith("{"):  # SSE framing: take the data line
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    result = json.loads(text)["result"]
    content = result["content"][0]["text"]
    return {"status": response.status_code, "body": response.text, "error": result.get("isError"), "json": content}


def _rest(client: Any, key: str, route: str) -> dict[str, Any]:
    response = client.get(f"/api{route}", headers={"X-API-Key": key})
    return {"status": response.status_code, "body": response.text}


def main(out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data
    endpoint = _upstream()

    from starlette.testclient import TestClient

    import mcp_hangar
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.cli.services.pinning import digest_tools

    config_file = out.parent / "config.yaml"
    config_file.write_text(json.dumps(_config(endpoint)))  # JSON is YAML
    context = bootstrap(config_path=str(config_file))
    keys = _keys(context)

    report: dict[str, Any] = {"hangar": mcp_hangar.__file__}
    with TestClient(_served_app(context), base_url=BASE_URL) as client:
        report["hangar_tools"] = {tenant: _hangar_tools(client, keys[tenant]) for tenant in (TENANT_A, TENANT_B)}
        report["rest"] = {
            caller: {
                "GET /api/tools": _rest(client, keys[caller], "/tools/"),
                "GET /api/mcp_servers/{id}/tools": _rest(client, keys[caller], f"/mcp_servers/{SERVER}/tools"),
            }
            for caller in ("ops", "scoped")
        }
        # What `mcp-hangar pin` would write for the server that served those listings.
        report["pin_cli"] = digest_tools(context.runtime.repository.get(SERVER))

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
