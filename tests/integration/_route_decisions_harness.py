"""Bootstrap Hangar with a canary-configured group, call it through the served app, and report the call's spans.

Run as a script, in its own interpreter, by ``test_route_decisions_on_the_served_app.py``:
``python _route_decisions_harness.py <topology> <out.json>``. Not collected by pytest.

It runs in a separate process for the reason ``_front_door_member_groups_harness.py``
gives: ``bootstrap()`` fills process-global state, and a second bootstrap in the
same interpreter would inherit it.

What runs is production, except the executor's tracer, which hands out spans
from an in-memory SDK provider:

- ``bootstrap()``, reading a config file that declares the topology and the
  group ``pool`` = {route-a, route-b}, priority strategy, with ``tenant-a``
  pinned to ``route-b`` under ``canary:``. API-key auth is on;
- on the front door, the catalogue warm-up ``run_http`` runs at boot;
- the app ``serve --http`` serves, wrapped in the auth enforcement ``run_http``
  applies, under starlette's ``TestClient``;
- two in-process HTTP MCP upstreams, one per member, that answer every ``tools/call``.

Each tenant calls ``whoami`` once: on egress through ``hangar_call`` naming
``pool``, on the front door as the flat tool, which the front door routes
through the member's group. The report holds, per tenant, the call's route keys,
``mcp.server.id`` and ``hangar.route.backend`` on each span the executor
opened, and which upstream answered.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
FRONT_DOOR = "front_door"

GROUP, MEMBER_A, MEMBER_B = "pool", "route-a", "route-b"
TENANTS = ("tenant-a", "tenant-b")
TOOL = "whoami"
EXECUTOR_SPANS = (
    f"batch.call.{TOOL}",
    "policy.check_access",
    "concurrency.acquire",
    "mcp_server.cold_start",
    "command.send.InvokeToolCommand",
)

_LOCK = threading.Lock()


class _Upstream(BaseHTTPRequestHandler):
    """An MCP upstream that answers every ``tools/call`` and counts them."""

    reached: ClassVar[list[str]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if "id" not in request:  # a notification
            self._send(202, b"")
            return
        method = request.get("method")
        params = request.get("params") or {}
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
            answer = {"result": {"tools": [{"name": TOOL, "inputSchema": {"type": "object"}}]}}
        elif method == "tools/call":
            with _LOCK:
                self.reached.append(str(params.get("name")))
            answer = {"result": {"content": [{"type": "text", "text": "ok"}]}}
        else:
            answer = {"error": {"code": -32601, "message": f"Unknown method: {method}"}}
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _upstream() -> tuple[str, type[_Upstream]]:
    handler: type[_Upstream] = type("_ThisUpstream", (_Upstream,), {"reached": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/mcp", handler


def _config(topology: str, endpoint_a: str, endpoint_b: str) -> dict[str, Any]:
    return {
        "tool_access": {"mode": topology},
        "rate_limit": {"rps": 1000, "burst": 1000},
        "auth": {
            "enabled": True,
            "allow_anonymous": False,
            "api_key": {"enabled": True, "header_name": "X-API-Key"},
            "storage": {"driver": "memory"},
        },
        "mcp_servers": {
            MEMBER_A: {"mode": "remote", "endpoint": endpoint_a},
            MEMBER_B: {"mode": "remote", "endpoint": endpoint_b},
            GROUP: {
                "mode": "group",
                "strategy": "priority",
                "min_healthy": 1,
                "members": [{"id": MEMBER_A, "priority": 1}, {"id": MEMBER_B, "priority": 2}],
                "canary": {"pinned_tenants": {TENANTS[0]: MEMBER_B}},
            },
        },
    }


def _jsonrpc(text: str) -> dict[str, Any]:
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return dict(json.loads(stripped))
    for line in text.splitlines():
        if line.startswith("data: "):
            return dict(json.loads(line[len("data: ") :]))
    return {"unparsed": text[:300]}


def _post(client: Any, key: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "MCP-Protocol-Version": MODERN_VERSION,
        "Mcp-Method": method,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "X-API-Key": key,
    }
    if method == "tools/call":
        headers["Mcp-Name"] = params["name"]
    envelope = {
        "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "route-decisions-harness", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": envelope}}
    return _jsonrpc(client.post("/mcp", headers=headers, content=json.dumps(body)).text)


def _call(client: Any, key: str, topology: str) -> str:
    """One call of ``whoami``: the payload's summary, for a failure message."""
    if topology == FRONT_DOOR:
        payload = _post(client, key, "tools/call", {"name": TOOL, "arguments": {}})
    else:
        calls = {"calls": [{"mcp_server": GROUP, "tool": TOOL, "arguments": {}}]}
        payload = _post(client, key, "tools/call", {"name": "hangar_call", "arguments": calls})
    return json.dumps(payload)[:300]


def _keys(context: Any) -> dict[str, str]:
    """One key per tenant, for a principal holding `developer` (which grants `tool:invoke`)."""
    auth = context.auth_components
    keys = {}
    for tenant in TENANTS:
        principal = f"svc:{tenant}"
        keys[tenant] = auth.api_key_store.create_key(principal_id=principal, name=tenant, tenant_id=tenant)
        auth.role_store.assign_role(principal, "developer")
    return keys


def main(topology: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data
    endpoint_a, upstream_a = _upstream()
    endpoint_b, upstream_b = _upstream()

    from unittest.mock import patch

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from starlette.testclient import TestClient

    import mcp_hangar
    from mcp_hangar.observability.conventions import McpServer, Route
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import mcp_app_for_serving, warm_the_front_door_catalogue

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))

    config_file = out.parent / "config.yaml"
    config_file.write_text(json.dumps(_config(topology, endpoint_a, endpoint_b)))  # JSON is YAML
    context = bootstrap(config_path=str(config_file))
    warm_the_front_door_catalogue(context.runtime)
    keys = _keys(context)
    app = create_auth_enforced_app(mcp_app_for_serving(context.mcp_server), context.auth_components)

    report: dict[str, dict[str, Any]] = {}
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_tracer", return_value=provider.get_tracer("harness")),
        TestClient(app, base_url=BASE_URL) as client,
    ):
        for tenant in TENANTS:
            memory.clear()
            upstreams = ((MEMBER_A, upstream_a), (MEMBER_B, upstream_b))
            before = {member: len(upstream.reached) for member, upstream in upstreams}
            answer = _call(client, keys[tenant], topology)
            spans = memory.get_finished_spans()
            calls = [s for s in spans if s.name == f"batch.call.{TOOL}"]
            route = dict(calls[0].attributes) if len(calls) == 1 else {}
            report[tenant] = {
                "answer": answer,
                "call_spans": len(calls),
                "reason": route.get(Route.REASON),
                "backend": route.get(Route.BACKEND),
                "spans": [
                    [s.name, s.attributes.get(McpServer.ID), s.attributes.get(Route.BACKEND)]
                    for s in spans
                    if s.name in EXECUTOR_SPANS
                ],
                "reached": [member for member, upstream in upstreams if len(upstream.reached) > before[member]],
            }

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps({"hangar": mcp_hangar.__file__, "report": report}))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
