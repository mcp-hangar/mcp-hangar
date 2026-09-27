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

A third upstream answers in SEP-2663's flat task shape, ``resultType: "task"``,
where the other two nest the task (#1405). Every call above is made by a caller
that declared the tasks extension. Each tenant then calls ``job`` and
``flat_job`` again without declaring it: a caller that cannot poll a task must
not be handed one.

A fourth upstream, ``job-spec``, is the one a spec-following server actually is
(#1492): it creates a task only when the request's ``_meta`` declares the tasks
extension, and answers an ordinary tool result otherwise. It is what shows that
the caller's declaration reached the upstream at all -- the other three create a
task unasked, so they cannot. Each tenant calls its ``spec_job`` twice, once
declaring and once not.

The upstreams also record every ``tasks/cancel`` they are sent. A task refused
to a caller that cannot poll it is one nobody will ever collect, so the seam
asks its upstream to cancel it; the report waits for those cancels rather than
racing them, since they are sent off the request path.

The report holds, for each tenant and tool: the call's outcome, whether the
call reached an upstream, and what ``tasks/get`` answered for the task it was
handed. It also holds the undeclared calls' outcomes, the spec upstream's
undeclared call, the id of every task the governed task store recorded, and the
id of every task an upstream was asked to cancel.

Run as ``python _front_door_task_governance_harness.py <topology> <out.json>
follow_ups`` it traces a task's follow-ups instead, for
``test_task_follow_ups_link_to_origin_on_the_served_app.py`` (#1281). An
in-memory exporter is registered on a provider before ``bootstrap()``, which
then uses it rather than building its own. One caller creates a task with
``job`` -- the flat call on the front door, ``hangar_call`` on egress -- then
polls, updates and cancels it in separate requests. The other tenant polls it
between, and the owner polls it once more after the cancel. A caller that did
not declare the extension then calls ``job``, so the seam cancels a task
nobody is handed. The report holds each answer and every span exported.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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
#: A server in no group whose upstream answers in SEP-2663's flat task shape (#1405).
FLAT_SERVER = "job-flat"
FLAT_TOOLS = ("flat_job",)
#: A server in no group whose upstream creates a task only for a caller that
#: declared the tasks extension, as SEP-2663 says one does (#1492).
SPEC_SERVER = "job-spec"
SPEC_TOOLS = ("spec_job",)

_TASK_IDS = itertools.count(1)
_LOCK = threading.Lock()


class _TaskUpstream(BaseHTTPRequestHandler):
    """An MCP upstream whose every ``tools/call`` answers with a new task handle."""

    tools: ClassVar[tuple[str, ...]] = ()
    #: Answer in SEP-2663's flat shape, ``resultType: "task"``, rather than the nested one.
    flat: ClassVar[bool] = False
    #: Create a task only for a caller that declared the tasks extension, as
    #: SEP-2663 says an upstream does. The others create one unasked.
    spec_only: ClassVar[bool] = False
    #: What ``initialize`` reports. A revision Hangar treats as legacy makes it
    #: withhold the whole protocol envelope, the caller's declaration included.
    #: NOT ``protocol_version``: that name is ``BaseHTTPRequestHandler``'s own,
    #: for the HTTP version it answers in, and setting it to an MCP revision
    #: makes every response an unparseable status line.
    mcp_protocol_version: ClassVar[str] = "2025-06-18"
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
        # The wire name only: an id relayed under any other name finds no task (#1617).
        followed = params.get("taskId")
        answer: dict[str, Any]
        if method == "initialize":
            answer = {
                "result": {
                    "protocolVersion": self.mcp_protocol_version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "task-upstream", "version": "0"},
                }
            }
        elif method == "tools/list":
            answer = {"result": {"tools": [{"name": name, "inputSchema": {"type": "object"}} for name in self.tools]}}
        elif method == "tools/call":
            created: dict[str, Any] | None = None
            with _LOCK:
                name = str(params.get("name"))
                self.reached.append(name)
                if not self.spec_only or _declares_tasks(params):
                    created = {
                        "taskId": f"task-{name}-{next(_TASK_IDS)}",
                        "status": "working",
                        "createdAt": "2020-01-01T00:00:00Z",
                        "lastUpdatedAt": "2020-01-01T00:00:00Z",
                        "ttl": 60_000,
                    }
                    self.tasks[created["taskId"]] = created
            if created is None:
                # What SEP-2663 has a spec-following upstream answer a caller that
                # declared nothing: an ordinary tool result, and no task.
                answer = {"result": {"content": [{"type": "text", "text": "done"}]}}
            else:
                answer = {"result": _flat(created, "task") if self.flat else {"task": created}}
        elif method == "tasks/get" and followed in self.tasks:
            polled = self.tasks[followed]
            answer = {"result": _flat(polled, "complete") if self.flat else polled}
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


def _flat(task: dict[str, Any], result_type: str) -> dict[str, Any]:
    """*task* in SEP-2663's flat shape: its fields at the top level, ``ttl`` as ``ttlMs``."""
    fields = {key: value for key, value in task.items() if key != "ttl"}
    return {"resultType": result_type, **fields, "ttlMs": task["ttl"]}


def _declares_tasks(params: dict[str, Any]) -> bool:
    """Did this request's ``_meta`` declare the tasks extension, as a client does?

    What SEP-2663 has an upstream gate task creation on, and what Hangar relays
    on its caller's behalf when the caller can poll a task.
    """
    from mcp_hangar.tasks_wire import EXTENSION_ID

    meta = params.get("_meta") or {}
    capabilities = meta.get("io.modelcontextprotocol/clientCapabilities") or {}
    extensions = capabilities.get("extensions") if isinstance(capabilities, dict) else None
    return isinstance(extensions, dict) and EXTENSION_ID in extensions


def _upstream(
    tools: tuple[str, ...],
    *,
    flat: bool = False,
    spec_only: bool = False,
    mcp_protocol_version: str = "2025-06-18",
) -> tuple[str, type[_TaskUpstream]]:
    """Serve an upstream exposing *tools*: its endpoint, and its handler class holding what it saw."""
    handler: type[_TaskUpstream] = type(
        "_ThisUpstream",
        (_TaskUpstream,),
        {
            "tools": tools,
            "flat": flat,
            "spec_only": spec_only,
            "mcp_protocol_version": mcp_protocol_version,
            "reached": [],
            "tasks": {},
            "follow_ups": [],
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/mcp", handler


def _config(
    topology: str,
    group_endpoint: str,
    solo_endpoint: str,
    flat_endpoint: str | None = None,
    spec_endpoint: str | None = None,
) -> dict[str, Any]:
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
            **({FLAT_SERVER: {"mode": "remote", "endpoint": flat_endpoint}} if flat_endpoint else {}),
            **({SPEC_SERVER: {"mode": "remote", "endpoint": spec_endpoint}} if spec_endpoint else {}),
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


def _tasks_capability() -> dict[str, Any]:
    """What a caller declares to be handed a task it can poll (#1405)."""
    from mcp_hangar.tasks_wire import EXTENSION_ID

    return {"extensions": {EXTENSION_ID: {}}}


def _flat_call(client: Any, key: str, tool: str, capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
    """A front door's flat ``tools/call`` of *tool*.

    Made by a caller that declared the tasks extension, unless *capabilities* say otherwise.
    """
    capabilities = _tasks_capability() if capabilities is None else capabilities
    payload = _post(client, key, "tools/call", {"name": tool, "arguments": {}}, name=tool, capabilities=capabilities)
    if "error" in payload:
        return {"outcome": f"error {payload['error'].get('code')}"}
    result = payload.get("result") or {}
    if result.get("resultType") == "task":
        return {"outcome": "task", "task_id": result.get("taskId")}
    if result.get("isError"):
        return {"outcome": "refused", "detail": result["content"][0]["text"]}
    return {"outcome": "ok", "detail": json.dumps(result)[:300]}


def _hangar_call(
    client: Any, key: str, target: str, tool: str, capabilities: dict[str, Any] | None = None
) -> dict[str, Any]:
    """``hangar_call`` of *tool* on *target*.

    Made by a caller that declared the tasks extension, unless *capabilities* say otherwise.
    """
    capabilities = _tasks_capability() if capabilities is None else capabilities
    call = {"calls": [{"mcp_server": target, "tool": tool, "arguments": {}}]}
    params = {"name": "hangar_call", "arguments": call}
    payload = _post(client, key, "tools/call", params, name="hangar_call", capabilities=capabilities)
    if "error" in payload:
        return {"outcome": f"error {payload['error'].get('code')}"}
    batch = json.loads(payload["result"]["content"][0]["text"])
    if "results" not in batch:
        return {"outcome": "refused", "detail": f"batch:{batch.get('error')}"}
    [result] = batch["results"]
    if not result["success"]:
        return {"outcome": "refused", "detail": str(result["error_type"])}
    upstream = result.get("result")
    if isinstance(upstream, dict) and upstream.get("resultType") == "task":  # SEP-2663's flat shape
        return {"outcome": "task", "task_id": upstream.get("taskId")}
    task = upstream.get("task") if isinstance(upstream, dict) else None
    if isinstance(task, dict):
        return {"outcome": "task", "task_id": task.get("taskId")}
    return {"outcome": "ok", "detail": json.dumps(upstream)[:300]}


def _target(tool: str) -> str:
    """The server ``hangar_call`` names for *tool*."""
    if tool in GROUP_TOOLS:
        return GROUP
    if tool in FLAT_TOOLS:
        return FLAT_SERVER
    return SPEC_SERVER if tool in SPEC_TOOLS else SOLO


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


def _serve(topology: str, out: Path, config: dict[str, Any]) -> tuple[Any, dict[str, str], Any]:
    """Bootstrap from *config* as ``serve --http`` does: the context, a key per tenant, and the served app."""
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import mcp_app_for_serving, warm_the_front_door_catalogue

    # A config file, as `serve --http` reads one. `tool_access.mode` is applied
    # while the file is loaded, so a config dict would leave the default topology.
    config_file = out.parent / "config.yaml"
    config_file.write_text(json.dumps(config))  # JSON is YAML
    context = bootstrap(config_path=str(config_file))
    # What `run_http` starts at boot. It returns at once on egress.
    warm_the_front_door_catalogue(context.runtime)
    keys = _keys(context)
    app = create_auth_enforced_app(mcp_app_for_serving(context.mcp_server), context.auth_components)
    return context, keys, app


def main(topology: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data
    group_endpoint, group_upstream = _upstream(GROUP_TOOLS)
    solo_endpoint, solo_upstream = _upstream(SOLO_TOOLS)
    flat_endpoint, flat_upstream = _upstream(FLAT_TOOLS, flat=True)
    # A current-spec upstream: it reports a revision Hangar sends the protocol
    # envelope to, and creates a task only for a caller that declared the
    # extension in it.
    spec_endpoint, spec_upstream = _upstream(SPEC_TOOLS, spec_only=True, mcp_protocol_version=MODERN_VERSION)

    from starlette.testclient import TestClient

    import mcp_hangar
    from mcp_hangar.server.context import get_context
    from mcp_hangar.tasks_wire import EXTENSION_ID

    config = _config(topology, group_endpoint, solo_endpoint, flat_endpoint, spec_endpoint)
    context, keys, app = _serve(topology, out, config)
    tasks_capability = {"extensions": {EXTENSION_ID: {}}}

    upstreams = (group_upstream, solo_upstream, flat_upstream, spec_upstream)

    def reached() -> int:
        return sum(len(upstream.reached) for upstream in upstreams)

    def cancelled() -> list[str]:
        """Every task an upstream has been asked to cancel so far."""
        with _LOCK:
            return sorted(
                task_id for upstream in upstreams for method, task_id in upstream.follow_ups if method == "tasks/cancel"
            )

    report: dict[str, dict[str, dict[str, Any]]] = {}
    undeclared: dict[str, dict[str, dict[str, Any]]] = {}
    spec_undeclared: dict[str, dict[str, Any]] = {}
    with TestClient(app, base_url=BASE_URL) as client:

        def call(tenant: str, tool: str, capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
            before = reached()
            if topology == FRONT_DOOR:
                outcome = _flat_call(client, keys[tenant], tool, capabilities)
            else:
                outcome = _hangar_call(client, keys[tenant], _target(tool), tool, capabilities)
            outcome["reached_upstream"] = reached() > before
            return outcome

        for tenant in TENANTS:
            calls = {tool: call(tenant, tool) for tool in (*GROUP_TOOLS, *SOLO_TOOLS, *FLAT_TOOLS, *SPEC_TOOLS)}
            for outcome in calls.values():
                if outcome.get("task_id"):
                    outcome["polled"] = _poll(client, keys[tenant], outcome["task_id"], tasks_capability)
            report[tenant] = calls
            # Allowed tools again, by a caller that did not declare the tasks extension (#1405).
            undeclared[tenant] = {tool: call(tenant, tool, capabilities={}) for tool in ("job", *FLAT_TOOLS)}
            # The spec upstream again, undeclared: it is the one that then makes
            # no task, which is what shows the declaration is what reaches it.
            spec_undeclared[tenant] = call(tenant, SPEC_TOOLS[0], capabilities={})

    # The cancel for a task no caller is handed is best effort and sent off the
    # request path, so wait for it rather than racing it (#1492): `job` and
    # `flat_job`, refused to an undeclared caller, for each tenant.
    expected_cancels = 2 * len(TENANTS)
    deadline = time.monotonic() + 15
    while len(cancelled()) < expected_cancels and time.monotonic() < deadline:
        time.sleep(0.05)
    cancels = cancelled()

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    # Every task the store recorded, to compare with the tasks callers were handed.
    recorded = sorted(task_id for _server, task_id in get_context().governed_task_store._tasks)
    out.write_text(
        json.dumps(
            {
                "hangar": mcp_hangar.__file__,
                "calls": report,
                "undeclared": undeclared,
                "spec_undeclared": spec_undeclared,
                "recorded": recorded,
                "cancelled": cancels,
            }
        )
    )
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


def _span(span: Any) -> dict[str, Any]:
    """An exported span as the parent test reads it: ids in hex, links as ``(trace, span)``."""
    return {
        "name": span.name,
        "kind": span.kind.name,
        "trace_id": f"{span.context.trace_id:032x}",
        "span_id": f"{span.context.span_id:016x}",
        "parent": f"{span.parent.span_id:016x}" if span.parent is not None else None,
        "links": [[f"{link.context.trace_id:032x}", f"{link.context.span_id:016x}"] for link in span.links],
        "status": span.status.status_code.name,
        "status_description": span.status.description,
        "attributes": dict(span.attributes or {}),
        "events": [{"name": e.name, "attributes": dict(e.attributes or {})} for e in span.events],
    }


def follow_ups(topology: str, out: Path) -> None:
    """Create a task, follow it up in separate requests, and export every span (#1281)."""
    os.chdir(out.parent)
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    spans = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))
    trace.set_tracer_provider(provider)

    group_endpoint, group_upstream = _upstream(GROUP_TOOLS)
    solo_endpoint, _solo = _upstream(SOLO_TOOLS)

    from starlette.testclient import TestClient

    import mcp_hangar

    context, keys, app = _serve(topology, out, _config(topology, group_endpoint, solo_endpoint))
    owner, other = keys[TENANTS[0]], keys[TENANTS[1]]
    capability = _tasks_capability()
    answers: dict[str, Any] = {}
    with TestClient(app, base_url=BASE_URL) as client:

        def create(capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
            if topology == FRONT_DOOR:
                return _flat_call(client, owner, "job", capabilities)
            return _hangar_call(client, owner, GROUP, "job", capabilities)

        def follow(key: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
            return _post(client, key, method, params, name=task_id, capabilities=capability)

        created = create()
        task_id = str(created.get("task_id"))
        answers["created"] = created
        answers["get"] = follow(owner, "tasks/get", {"taskId": task_id})
        answers["update"] = follow(owner, "tasks/update", {"taskId": task_id, "inputResponses": {"r1": {"ok": True}}})
        answers["foreign_get"] = follow(other, "tasks/get", {"taskId": task_id})
        answers["cancel"] = follow(owner, "tasks/cancel", {"taskId": task_id})
        answers["get_after_cancel"] = follow(owner, "tasks/get", {"taskId": task_id})
        answers["unhanded"] = create(capabilities={})

    # The seam's cancel of the unhanded task is sent off the request path; wait for its span.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if any(s.name == "task_relay.cancel_unhanded" for s in spans.get_finished_spans()):
            break
        time.sleep(0.05)

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(
        json.dumps(
            {
                "hangar": mcp_hangar.__file__,
                "task_id": task_id,
                "answers": answers,
                "upstream_follow_ups": [list(item) for item in group_upstream.follow_ups],
                "spans": [_span(s) for s in spans.get_finished_spans()],
            },
            default=str,
        )
    )
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    if sys.argv[3:] == ["follow_ups"]:
        follow_ups(sys.argv[1], Path(sys.argv[2]))
    else:
        main(sys.argv[1], Path(sys.argv[2]))
