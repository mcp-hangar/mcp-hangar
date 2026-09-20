"""Bootstrap Hangar with a server in two groups, then call its tools through the served app, on one topology.

Run as a script, in its own interpreter, by
``test_a_front_door_member_of_several_groups_is_governed_by_each.py``:
``python _front_door_member_groups_harness.py <topology> <order> <out.json>``.
Not collected by pytest.

It runs in a separate process for the reason ``_group_recovery_harness.py``
gives. ``bootstrap()`` fills process-global state (the runtime, ``GROUPS``, the
resolver, the projection registry), and a second bootstrap in the same
interpreter would inherit it.

What runs is production:

- ``bootstrap()``, reading a config file that declares the topology, each
  group's policy, withdrawals and pin. API-key auth is on;
- on the front door, the catalogue warm-up ``run_http`` runs at boot;
- the app ``serve --http`` serves, wrapped in the auth enforcement ``run_http``
  applies, under starlette's ``TestClient``;
- two in-process HTTP MCP upstreams that answer every ``tools/call``.

``shared-member`` is a member of ``pool-a`` and of ``pool-b``. ``<order>`` is
``ab`` or ``ba``: which of the two groups the file declares first.
``single-member`` is the one member of ``pool-c``.

Each call presents an API key minted in the bootstrapped store for a principal
holding ``developer``, which grants ``tool:invoke``. On the front door each
caller lists its tools, then makes the flat ``tools/call`` of every tool. On
egress it is ``hangar_call`` naming ``shared-member`` for that member's tools,
and ``pool-c`` for the single-group member's: the call the front door makes
for each.

One thing is changed, and it is not on the path under test: ``rate_limit`` is
raised, as ``_member_direct_governance_harness.py`` raises it.

The report holds, for each tenant: the names it was listed (front door only),
and for each tool the call's decision, its detail, and whether it reached an
upstream.
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

SHARED, SINGLE = "shared-member", "single-member"
POOL_A, POOL_B, POOL_C = "pool-a", "pool-b", "pool-c"
TENANTS = ("tenant-a", "tenant-b")
#: Matches no schema, so pool-b's pin on `b_pinned` refuses every call that reaches it.
STALE_DIGEST = "a" * 64

#: What each upstream serves. The names differ, so the flat names do not collide.
SHARED_TOOLS = ("shared_ok", "a_denied", "b_denied", "b_withdrawn", "b_withdrawn_for_a", "b_pinned")
SINGLE_TOOLS = ("single_ok", "c_denied", "c_withdrawn")

_LOCK = threading.Lock()


class _Upstream(BaseHTTPRequestHandler):
    """An MCP upstream that answers every ``tools/call``."""

    tools: ClassVar[tuple[str, ...]] = ()
    #: The tools a ``tools/call`` reached, in order.
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
            answer = {"result": {"tools": [{"name": name, "inputSchema": {"type": "object"}} for name in self.tools]}}
        elif method == "tools/call":
            with _LOCK:
                self.reached.append(str(params.get("name")))
            answer = {"result": {"content": [{"type": "text", "text": f"did {params.get('name')}"}]}}
        else:
            answer = {"error": {"code": -32601, "message": f"Unknown method: {method}"}}
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _upstream(tools: tuple[str, ...]) -> tuple[str, type[_Upstream]]:
    """Serve an upstream exposing *tools*: its endpoint, and its handler class holding what it saw."""
    handler: type[_Upstream] = type("_ThisUpstream", (_Upstream,), {"tools": tools, "reached": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/mcp", handler


def _pool(member: str, **declared: Any) -> dict[str, Any]:
    return {"mode": "group", "strategy": "priority", "min_healthy": 1, "members": [{"id": member}], **declared}


def _config(topology: str, order: str, shared_endpoint: str, single_endpoint: str) -> dict[str, Any]:
    pools = {
        POOL_A: _pool(SHARED, tools={"deny_list": ["a_denied"]}),
        POOL_B: _pool(
            SHARED,
            tools={"deny_list": ["b_denied"]},
            tool_projection={
                "digest_enforcement": "block",
                "pins": {"b_pinned": STALE_DIGEST},
                "withdrawn": ["b_withdrawn"],
                "tenant_overrides": {TENANTS[0]: {"withdrawn": ["b_withdrawn_for_a"]}},
            },
        ),
    }
    first, second = (POOL_A, POOL_B) if order == "ab" else (POOL_B, POOL_A)
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
            SHARED: {"mode": "remote", "endpoint": shared_endpoint},
            SINGLE: {"mode": "remote", "endpoint": single_endpoint},
            first: pools[first],
            second: pools[second],
            POOL_C: _pool(SINGLE, tools={"deny_list": ["c_denied"]}, tool_projection={"withdrawn": ["c_withdrawn"]}),
        },
    }


def _jsonrpc(text: str) -> dict[str, Any]:
    """The JSON-RPC payload, from plain JSON or from an SSE ``data:`` frame."""
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
        "io.modelcontextprotocol/clientInfo": {"name": "member-groups-harness", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": envelope}}
    return _jsonrpc(client.post("/mcp", headers=headers, content=json.dumps(body)).text)


def _flat_call(client: Any, key: str, tool: str) -> dict[str, Any]:
    """A front door's flat ``tools/call`` of *tool*."""
    payload = _post(client, key, "tools/call", {"name": tool, "arguments": {}})
    if "error" in payload:
        return {"decision": "refused", "detail": f"error {payload['error'].get('code')}"}
    result = payload.get("result") or {}
    if result.get("isError"):
        return {"decision": "refused", "detail": result["content"][0]["text"]}
    return {"decision": "ok", "detail": json.dumps(result)[:300]}


def _hangar_call(client: Any, key: str, target: str, tool: str) -> dict[str, Any]:
    """``hangar_call`` of *tool* on *target*."""
    call = {"calls": [{"mcp_server": target, "tool": tool, "arguments": {}}]}
    payload = _post(client, key, "tools/call", {"name": "hangar_call", "arguments": call})
    if "error" in payload:
        return {"decision": "refused", "detail": f"error {payload['error'].get('code')}"}
    batch = json.loads(payload["result"]["content"][0]["text"])
    if "results" not in batch:
        return {"decision": "refused", "detail": f"batch:{batch.get('error')}"}
    [result] = batch["results"]
    if not result["success"]:
        return {"decision": "refused", "detail": str(result["error_type"])}
    return {"decision": "ok", "detail": json.dumps(result.get("result"))[:300]}


def _keys(context: Any) -> dict[str, str]:
    """One key per tenant, for a principal holding `developer` (which grants `tool:invoke`)."""
    auth = context.auth_components
    keys = {}
    for tenant in TENANTS:
        principal = f"svc:{tenant}"
        keys[tenant] = auth.api_key_store.create_key(principal_id=principal, name=tenant, tenant_id=tenant)
        auth.role_store.assign_role(principal, "developer")
    return keys


def main(topology: str, order: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data
    shared_endpoint, shared_upstream = _upstream(SHARED_TOOLS)
    single_endpoint, single_upstream = _upstream(SINGLE_TOOLS)

    from starlette.testclient import TestClient

    import mcp_hangar
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import mcp_app_for_serving, warm_the_front_door_catalogue

    # A config file, as `serve --http` reads one. `tool_access.mode` is applied
    # while the file is loaded, so a config dict would leave the default topology.
    config_file = out.parent / "config.yaml"
    config_file.write_text(json.dumps(_config(topology, order, shared_endpoint, single_endpoint)))  # JSON is YAML
    context = bootstrap(config_path=str(config_file))
    # What `run_http` starts at boot. It returns at once on egress.
    warm_the_front_door_catalogue(context.runtime)
    keys = _keys(context)
    app = create_auth_enforced_app(mcp_app_for_serving(context.mcp_server), context.auth_components)

    def reached() -> int:
        return len(shared_upstream.reached) + len(single_upstream.reached)

    report: dict[str, dict[str, Any]] = {}
    with TestClient(app, base_url=BASE_URL) as client:
        for tenant in TENANTS:
            listed: list[str] | None = None
            if topology == FRONT_DOOR:
                # The upstreams' tools: the control plane's own are listed beside them.
                listing = _post(client, keys[tenant], "tools/list", {})
                upstream_tools = {*SHARED_TOOLS, *SINGLE_TOOLS}
                listed = sorted(tool["name"] for tool in listing["result"]["tools"] if tool["name"] in upstream_tools)
            calls: dict[str, dict[str, Any]] = {}
            for tool in (*SHARED_TOOLS, *SINGLE_TOOLS):
                before = reached()
                if topology == FRONT_DOOR:
                    outcome = _flat_call(client, keys[tenant], tool)
                else:
                    outcome = _hangar_call(client, keys[tenant], SHARED if tool in SHARED_TOOLS else POOL_C, tool)
                outcome["reached_upstream"] = reached() > before
                calls[tool] = outcome
            report[tenant] = {"listed": listed, "calls": calls}

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps({"hangar": mcp_hangar.__file__, "report": report}))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
