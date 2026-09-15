"""Bootstrap Hangar, then call a group's task-creating tools through the served app, on one topology.

Run as a script, in its own interpreter, by
``test_a_front_door_task_call_is_governed_by_its_group.py``:
``python _front_door_task_governance_harness.py <topology> <out.json>``. Not
collected by pytest.

It runs in a separate process for the reason ``_group_recovery_harness.py``
gives. ``bootstrap()`` fills process-global state (the runtime, ``GROUPS``, the
resolver, the projection registry), and a second bootstrap in the same
interpreter would inherit it.

What runs is production:

- ``bootstrap()``, reading a config file that declares the topology, the
  group's policy, its withdrawals and the approval lists. API-key
  auth is on, and so is the governed task relay, which is its default;
- on the front door, the catalogue warm-up ``run_http`` runs at boot;
- the app ``serve --http`` serves, wrapped in the auth enforcement ``run_http``
  applies, under starlette's ``TestClient``;
- the approval gate ``bootstrap()`` wires, which holds a call until its timeout
  because nobody answers;
- two in-process HTTP MCP upstreams. Every ``tools/call`` they are sent answers
  with a new task handle, so every call that reaches one goes back to its
  caller through the relay seam.

Each call presents an API key minted in the bootstrapped store for a principal
holding ``developer``, which grants ``tool:invoke``. On the front door a call is
the flat ``tools/call`` of the tool's own name, which the front door dispatches
through the tool's group (#857). On egress it is ``hangar_call`` naming the
group, or the ungrouped server. Once every call is made, each caller polls each
task it was handed with ``tasks/get``.

One thing is changed, and it is not on the path under test: ``rate_limit`` is
raised, as ``_member_direct_governance_harness.py`` raises it.

The report holds, for each tenant and tool: the call's outcome, whether the
call reached an upstream, and what ``tasks/get`` answered for the task it was
handed.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import itertools
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any, ClassVar

BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
FRONT_DOOR = "front_door"

GROUP = "job-pool"
MEMBER = "job-a"
SIBLING = "job-b"
#: A server in no group.
SOLO = "job-solo"
TENANTS = ("tenant-a", "tenant-b")

#: What each upstream serves. The names differ, so the front door's flat names
#: do not collide across the group and the ungrouped server.
GROUP_TOOLS = ("job", "job_denied", "job_withdrawn", "job_withdrawn_a", "job_held")
SOLO_TOOLS = ("solo_job", "solo_held_a")

_TASK_IDS = itertools.count(1)
_LOCK = threading.Lock()


class _TaskUpstream(BaseHTTPRequestHandler):
    """An MCP upstream whose every ``tools/call`` answers with a new task handle."""

    tools: ClassVar[tuple[str, ...]] = ()
    #: The tools a ``tools/call`` reached, in order.
    reached: ClassVar[list[str]] = []
    tasks: ClassVar[dict[str, dict[str, Any]]] = {}
    #: The ``(method, task id)`` of each ``tasks/update`` and ``tasks/cancel`` it was sent.
    follow_ups: ClassVar[list[tuple[str, str]]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if "id" not in request:  # a notification
            self._send(202, b"")
            return
        method = request.get("method")
        params = request.get("params") or {}
        followed = params.get("taskId") or params.get("task_id")
        answer: dict[str, Any]
        if method == "initialize":
            answer = {
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "task-upstream", "version": "0"},
                }
            }
        elif method == "tools/list":
            answer = {"result": {"tools": [{"name": name, "inputSchema": {"type": "object"}} for name in self.tools]}}
        elif method == "tools/call":
            with _LOCK:
                name = str(params.get("name"))
                task = {
                    "taskId": f"task-{name}-{next(_TASK_IDS)}",
                    "status": "working",
                    "createdAt": "2020-01-01T00:00:00Z",
                    "lastUpdatedAt": "2020-01-01T00:00:00Z",
                    "ttl": 60_000,
                }
                self.reached.append(name)
                self.tasks[task["taskId"]] = task
            answer = {"result": {"task": task}}
        elif method == "tasks/get" and params.get("taskId") in self.tasks:
            answer = {"result": self.tasks[params["taskId"]]}
        elif method in ("tasks/update", "tasks/cancel") and followed in self.tasks:
            with _LOCK:
                self.follow_ups.append((method, followed))
                if method == "tasks/cancel":
                    self.tasks[followed]["status"] = "cancelled"
            answer = {"result": self.tasks[followed]}
        else:
            answer = {"error": {"code": -32601, "message": f"Unknown method: {method}"}}
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _upstream(tools: tuple[str, ...]) -> tuple[str, type[_TaskUpstream]]:
    """Serve an upstream exposing *tools*: its endpoint, and its handler class holding what it saw."""
    handler: type[_TaskUpstream] = type(
        "_ThisUpstream", (_TaskUpstream,), {"tools": tools, "reached": [], "tasks": {}, "follow_ups": []}
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/mcp", handler


def _config(topology: str, group_endpoint: str, solo_endpoint: str) -> dict[str, Any]:
    group: dict[str, Any] = {
        "mode": "group",
        "strategy": "priority",
        "min_healthy": 1,
        "tools": {"deny_list": ["job_denied"], "approval_list": ["job_held"], "approval_timeout_seconds": 1},
        "tool_projection": {
            "withdrawn": ["job_withdrawn"],
            "tenant_overrides": {TENANTS[0]: {"withdrawn": ["job_withdrawn_a"]}},
        },
        "members": [{"id": MEMBER, "priority": 1}, {"id": SIBLING, "priority": 2}],
    }
    solo: dict[str, Any] = {
        "mode": "remote",
        "endpoint": solo_endpoint,
        "tool_access": {"member": {TENANTS[0]: {"approval_list": ["solo_held_a"], "approval_timeout_seconds": 1}}},
    }
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
            MEMBER: {"mode": "remote", "endpoint": group_endpoint},
            SIBLING: {"mode": "remote", "endpoint": group_endpoint},
            SOLO: solo,
            GROUP: group,
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


def _post(
    client: Any, key: str, method: str, params: dict[str, Any], name: str, capabilities: dict[str, Any] | None = None
) -> dict[str, Any]:
    headers = {
        "MCP-Protocol-Version": MODERN_VERSION,
        "Mcp-Method": method,
        "Mcp-Name": name,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "X-API-Key": key,
    }
    envelope = {
        "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "front-door-task-harness", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": capabilities or {},
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": envelope}}
    return _jsonrpc(client.post("/mcp", headers=headers, content=json.dumps(body)).text)


def _flat_call(client: Any, key: str, tool: str) -> dict[str, Any]:
    """A front door's flat ``tools/call`` of *tool*."""
    payload = _post(client, key, "tools/call", {"name": tool, "arguments": {}}, name=tool)
    if "error" in payload:
        return {"outcome": f"error {payload['error'].get('code')}"}
    result = payload.get("result") or {}
    if result.get("resultType") == "task":
        return {"outcome": "task", "task_id": result.get("taskId")}
    if result.get("isError"):
        return {"outcome": "refused", "detail": result["content"][0]["text"]}
    return {"outcome": "ok", "detail": json.dumps(result)[:300]}


def _hangar_call(client: Any, key: str, target: str, tool: str) -> dict[str, Any]:
    """``hangar_call`` of *tool* on *target*."""
    call = {"calls": [{"mcp_server": target, "tool": tool, "arguments": {}}]}
    payload = _post(client, key, "tools/call", {"name": "hangar_call", "arguments": call}, name="hangar_call")
    if "error" in payload:
        return {"outcome": f"error {payload['error'].get('code')}"}
    batch = json.loads(payload["result"]["content"][0]["text"])
    if "results" not in batch:
        return {"outcome": "refused", "detail": f"batch:{batch.get('error')}"}
    [result] = batch["results"]
    if not result["success"]:
        return {"outcome": "refused", "detail": str(result["error_type"])}
    upstream = result.get("result")
    task = upstream.get("task") if isinstance(upstream, dict) else None
    if isinstance(task, dict):
        return {"outcome": "task", "task_id": task.get("taskId")}
    return {"outcome": "ok", "detail": json.dumps(upstream)[:300]}


def _poll(client: Any, key: str, task_id: str, capabilities: dict[str, Any]) -> str:
    """What ``tasks/get`` answers *key*'s caller for *task_id*: the task's status, or the error."""
    payload = _post(client, key, "tasks/get", {"taskId": task_id}, name=task_id, capabilities=capabilities)
    if "error" in payload:
        return f"error {payload['error'].get('message')}"
    return str((payload.get("result") or {}).get("status"))


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
    group_endpoint, group_upstream = _upstream(GROUP_TOOLS)
    solo_endpoint, solo_upstream = _upstream(SOLO_TOOLS)

    from starlette.testclient import TestClient

    import mcp_hangar
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import mcp_app_for_serving, warm_the_front_door_catalogue
    from mcp_hangar.tasks_wire import EXTENSION_ID

    # A config file, as `serve --http` reads one. `tool_access.mode` is applied
    # while the file is loaded, so a config dict would leave the default topology.
    config_file = out.parent / "config.yaml"
    config_file.write_text(json.dumps(_config(topology, group_endpoint, solo_endpoint)))  # JSON is YAML
    context = bootstrap(config_path=str(config_file))
    # What `run_http` starts at boot. It returns at once on egress.
    warm_the_front_door_catalogue(context.runtime)
    keys = _keys(context)
    app = create_auth_enforced_app(mcp_app_for_serving(context.mcp_server), context.auth_components)
    tasks_capability = {"extensions": {EXTENSION_ID: {}}}

    def reached() -> int:
        return len(group_upstream.reached) + len(solo_upstream.reached)

    report: dict[str, dict[str, dict[str, Any]]] = {}
    with TestClient(app, base_url=BASE_URL) as client:
        for tenant in TENANTS:
            calls: dict[str, dict[str, Any]] = {}
            for tool in (*GROUP_TOOLS, *SOLO_TOOLS):
                before = reached()
                if topology == FRONT_DOOR:
                    outcome = _flat_call(client, keys[tenant], tool)
                else:
                    outcome = _hangar_call(client, keys[tenant], GROUP if tool in GROUP_TOOLS else SOLO, tool)
                outcome["reached_upstream"] = reached() > before
                calls[tool] = outcome
            for outcome in calls.values():
                if outcome.get("task_id"):
                    outcome["polled"] = _poll(client, keys[tenant], outcome["task_id"], tasks_capability)
            report[tenant] = calls

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps({"hangar": mcp_hangar.__file__, "calls": report}))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
