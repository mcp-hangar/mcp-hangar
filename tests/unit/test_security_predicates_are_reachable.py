"""Every security predicate runs on the served paths it guards (#1386).

A predicate that exists and is never called is not a check. That is how a
suspended session kept invoking tools until 2.19.1 (GHSA-fhwh-fmq2-7m5c). This
file sends one real request down each served path, and fails when a predicate
registered in ``_security_predicates.PREDICATES`` does not run on a path it
declares.

What is driven is what ``mcp-hangar serve --http`` serves, composed the way
``bootstrap()`` and ``ServerLifecycle.run_http`` compose it:
``build_serving_mcp_server()``, the task relay ``bootstrap()`` adds to it
(``enable_governed_task_relay``), ``mcp_app_for_serving()`` and the auth
enforcement ``run_http`` puts in front (``create_auth_enforced_app``). The
requests go over streamable HTTP through starlette's ``TestClient``, with a real
API key, from loopback. The REST API and the WebSocket event stream are mounted
at ``/api`` beside it, as ``run_http`` mounts them (see
``tests/integration/test_rest_authz_on_served_app.py``), and are called by a
fleet-wide caller or, on a ``tenant_aware`` route, by one whose grant is
confined to its tenant. Every other route of that API is driven in a process of
its own (``_rest_route_probes.py``), and ``TestEveryRouteHasAProbe`` fails when a
rule or a served route has neither. Loopback is a trusted proxy by default, so the
``x-session-id`` every request carries gives the suspension check a session to
look up. The one upstream is an in-process HTTP MCP server. No handler, gate or
predicate is patched: ``test_tool_permissions_cover_the_surface.py`` is the
model, and the lesson of #596 and #963-#965 is that a surface assembled by hand
can agree with a table while production disagrees.

The facade's ``invoke`` and a stdio session are only served by a process that
ran ``bootstrap()`` for itself, and a second boot in this interpreter would read
the first's global state. The same goes for the routes that change the fleet,
and what those change must stay out of this process.
``_predicate_reachability_harness.py`` drives each in a process of its own, with
the same spy (``_predicate_spy``), and its recordings join this file's, so every
check below holds them to the same table.

The spy is installed before the client starts, so every thread the served app
starts inherits it.
"""

from __future__ import annotations

import inspect
import json
import re
import subprocess
import sys
import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from starlette.routing import Mount, Route, WebSocketRoute, compile_path

# First: `server.bootstrap` is the import order production uses (#894).
from mcp_hangar.server.bootstrap import build_serving_mcp_server
from mcp_hangar.tasks_wire import EXTENSION_ID
from mcp_hangar.server.api import create_api_router
from mcp_hangar.server.api.route_permissions import ROUTE_PERMISSIONS, RouteRule
from tests.unit._predicate_spy import Spy, from_json
from tests.unit._rest_route_probes import EXEMPT_ROUTES, ROUTE_PROBE_NAMES, ROUTE_PROBES, rule_of
from tests.unit._security_predicates import (
    COMPLETION,
    FACADE_INVOKE,
    FLAT_TOOL_CALL,
    FRONT_DOOR_HANGAR_DETAILS,
    FRONT_DOOR_HANGAR_TOOLS,
    FRONT_DOOR_MANAGEMENT_TOOL,
    FRONT_DOOR_TASKS_CANCEL,
    FRONT_DOOR_TASKS_GET,
    FRONT_DOOR_TASKS_UPDATE,
    HANGAR_CALL,
    HANGAR_DETAILS,
    HANGAR_TOOLS,
    MANAGEMENT_TOOL,
    PREDICATES,
    PROMPTS_GET,
    PROMPTS_LIST,
    RESOURCE_TEMPLATES_LIST,
    RESOURCES_LIST,
    RESOURCES_READ,
    REST_APPROVAL_RESOLVE,
    REST_APPROVALS,
    REST_CONFIG,
    REST_GROUPS,
    REST_PINS,
    REST_RESTORE,
    REST_SERVER,
    REST_SERVER_TOOLS,
    REST_SERVERS,
    REST_SESSION_LIFT,
    REST_SESSION_SUSPEND,
    REST_SYSTEM_ME,
    REST_TOOL_HISTORY,
    REST_TOOLS,
    REST_WITHDRAW,
    SERVER_DISCOVER,
    STDIO_FLAT_TOOL_CALL,
    STDIO_HANGAR_CALL,
    SUBSCRIPTIONS_LISTEN,
    TASKS_CANCEL,
    TASKS_GET,
    TASKS_UPDATE,
    WS_EVENTS,
    Predicate,
)

# The SDK's DNS-rebinding protection wants a loopback Host with a port.
_BASE_URL = "http://127.0.0.1:8000"
#: The peer address the served app sees. Loopback is in the default
#: `MCP_TRUSTED_PROXIES`, so the `x-session-id` header is honoured.
_PEER = ("127.0.0.1", 50000)
_MODERN = "2026-07-28"
_SESSION = "s-reachability"
_UPSTREAM = "upstream"
_TASK_ID = "task-reachability"
#: The tenant of every caller on every surface.
_TENANT = "tenant-a"


# --- the upstream --------------------------------------------------------------


_TASK = {
    "taskId": _TASK_ID,
    "status": "working",
    "createdAt": "2020-01-01T00:00:00Z",
    "lastUpdatedAt": "2020-01-01T00:00:00Z",
    "ttl": 60_000,
}

#: What the upstream answers, by method. `tools/call` is answered separately.
_ANSWERS: dict[str, dict[str, Any]] = {
    "initialize": {
        "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}, "prompts": {}, "resources": {}, "completions": {}},
        }
    },
    "tools/list": {
        "result": {
            "tools": [
                {"name": "add", "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}}}},
                {"name": "long_job", "inputSchema": {"type": "object"}},
            ]
        }
    },
    "prompts/list": {"result": {"prompts": [{"name": "greet"}]}},
    "prompts/get": {"result": {"messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}]}},
    "completion/complete": {"result": {"completion": {"values": ["bob"]}}},
    # A ui:// resource too, so the ui:// allowlist is asked about one.
    "resources/list": {
        "result": {"resources": [{"uri": "doc://one", "name": "one"}, {"uri": "ui://panel", "name": "p"}]}
    },
    "resources/templates/list": {"result": {"resourceTemplates": [{"uriTemplate": "doc://{id}", "name": "doc"}]}},
    "tasks/get": {"result": _TASK},
    # Unconfirmed, so the task is kept for `tasks/update`.
    "tasks/cancel": {"error": {"code": -32000, "message": "not yet"}},
    "tasks/update": {"result": _TASK},
}


class _Upstream(BaseHTTPRequestHandler):
    """A minimal MCP server over HTTP, answering every method the probes reach."""

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
            # `long_job` answers with a task handle, which is how a task comes
            # to exist for the `tasks/*` probes.
            is_task = params.get("name") == "long_job"
            answer = {"result": {"task": _TASK} if is_task else {"content": [{"type": "text", "text": "ok"}]}}
        elif method == "resources/read":
            answer = {"result": {"contents": [{"uri": params.get("uri", ""), "text": "body"}]}}
        else:
            answer = _ANSWERS.get(method, {"error": {"code": -32601, "message": "Method not found"}})
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def _upstream() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    finally:
        server.shutdown()
        server.server_close()


# --- the probes ------------------------------------------------------------------


@dataclass(frozen=True)
class _Probe:
    """One request down one served path."""

    topology: str
    #: The JSON-RPC method, or the HTTP method of a REST probe.
    method: str
    params: dict[str, Any]
    #: The SEP-2243 `Mcp-Name` routing header, where the method carries a name.
    name: str | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    #: The route, for a surface that is not the MCP endpoint (`server/discover`, `/api`).
    path: str = "/mcp"
    #: How it is sent: an MCP request, a `subscriptions/listen` stream read to
    #: its acknowledgement, a REST request, or a WebSocket session.
    surface: str = "mcp"
    #: Who sends it: the fleet-wide caller, or the one confined to its tenant.
    caller: str = "fleet"


def _hangar_call(tool: str) -> dict[str, Any]:
    return {
        "name": "hangar_call",
        "arguments": {"calls": [{"mcp_server": _UPSTREAM, "tool": tool, "arguments": {"a": 1}}]},
    }


_TASKS = {"extensions": {EXTENSION_ID: {}}}
_READ_URI = f"hangar://{_UPSTREAM}/doc://one"

#: Sent before each topology's probes, unrecorded: mints the task `tasks/*` act
#: on. On the front door the flat call mints it, so the front door's `tasks/*`
#: probes act on a task that path created (#1394). Its caller declares the tasks
#: extension: a task is handed only to a caller that can poll it (#1405).
_MINT_TASK = {
    "egress": _Probe("egress", "tools/call", _hangar_call("long_job"), name="hangar_call", capabilities=_TASKS),
    "front_door": _Probe(
        "front_door", "tools/call", {"name": "long_job", "arguments": {}}, name="long_job", capabilities=_TASKS
    ),
}

#: One probe per served path, in the order they are sent. `tasks/cancel` is
#: unconfirmed upstream, so the task survives it for `tasks/update`.
PROBES: dict[str, _Probe] = {
    HANGAR_CALL: _Probe("egress", "tools/call", _hangar_call("add"), name="hangar_call"),
    MANAGEMENT_TOOL: _Probe("egress", "tools/call", {"name": "hangar_list", "arguments": {}}, name="hangar_list"),
    TASKS_GET: _Probe("egress", "tasks/get", {"taskId": _TASK_ID}, name=_TASK_ID, capabilities=_TASKS),
    TASKS_CANCEL: _Probe("egress", "tasks/cancel", {"taskId": _TASK_ID}, name=_TASK_ID, capabilities=_TASKS),
    TASKS_UPDATE: _Probe(
        "egress", "tasks/update", {"taskId": _TASK_ID, "inputResponses": {}}, name=_TASK_ID, capabilities=_TASKS
    ),
    FLAT_TOOL_CALL: _Probe("front_door", "tools/call", {"name": "add", "arguments": {"a": 1}}, name="add"),
    FRONT_DOOR_MANAGEMENT_TOOL: _Probe(
        "front_door", "tools/call", {"name": "hangar_list", "arguments": {}}, name="hangar_list"
    ),
    FRONT_DOOR_TASKS_GET: _Probe("front_door", "tasks/get", {"taskId": _TASK_ID}, name=_TASK_ID, capabilities=_TASKS),
    FRONT_DOOR_TASKS_CANCEL: _Probe(
        "front_door", "tasks/cancel", {"taskId": _TASK_ID}, name=_TASK_ID, capabilities=_TASKS
    ),
    FRONT_DOOR_TASKS_UPDATE: _Probe(
        "front_door", "tasks/update", {"taskId": _TASK_ID, "inputResponses": {}}, name=_TASK_ID, capabilities=_TASKS
    ),
    PROMPTS_LIST: _Probe("front_door", "prompts/list", {}),
    PROMPTS_GET: _Probe("front_door", "prompts/get", {"name": "greet", "arguments": {}}, name="greet"),
    COMPLETION: _Probe(
        "front_door",
        "completion/complete",
        {"ref": {"type": "ref/prompt", "name": "greet"}, "argument": {"name": "who", "value": "b"}},
    ),
    RESOURCES_LIST: _Probe("front_door", "resources/list", {}),
    RESOURCE_TEMPLATES_LIST: _Probe("front_door", "resources/templates/list", {}),
    RESOURCES_READ: _Probe("front_door", "resources/read", {"uri": _READ_URI}, name=_READ_URI),
    HANGAR_TOOLS: _Probe(
        "egress", "tools/call", {"name": "hangar_tools", "arguments": {"mcp_server": _UPSTREAM}}, name="hangar_tools"
    ),
    HANGAR_DETAILS: _Probe(
        "egress",
        "tools/call",
        {"name": "hangar_details", "arguments": {"mcp_server": _UPSTREAM}},
        name="hangar_details",
    ),
    FRONT_DOOR_HANGAR_TOOLS: _Probe(
        "front_door",
        "tools/call",
        {"name": "hangar_tools", "arguments": {"mcp_server": _UPSTREAM}},
        name="hangar_tools",
    ),
    FRONT_DOOR_HANGAR_DETAILS: _Probe(
        "front_door",
        "tools/call",
        {"name": "hangar_details", "arguments": {"mcp_server": _UPSTREAM}},
        name="hangar_details",
    ),
    SERVER_DISCOVER: _Probe("front_door", "server/discover", {}, path="/server/discover"),
    SUBSCRIPTIONS_LISTEN: _Probe(
        "front_door",
        "subscriptions/listen",
        {"notifications": {"toolsListChanged": True, "resourceSubscriptions": [_READ_URI]}},
        surface="listen",
    ),
    # Last on egress: the MCP probes above are done before a tool is withdrawn,
    # and the withdrawal is restored by the probe after it.
    REST_SERVERS: _Probe("egress", "GET", {}, path="/api/mcp_servers", surface="rest"),
    REST_SERVER: _Probe("egress", "GET", {}, path=f"/api/mcp_servers/{_UPSTREAM}", surface="rest"),
    REST_SERVER_TOOLS: _Probe("egress", "GET", {}, path=f"/api/mcp_servers/{_UPSTREAM}/tools", surface="rest"),
    REST_TOOL_HISTORY: _Probe(
        "egress", "GET", {}, path=f"/api/mcp_servers/{_UPSTREAM}/tools/history", surface="rest", caller="tenant"
    ),
    REST_GROUPS: _Probe("egress", "GET", {}, path="/api/groups", surface="rest"),
    REST_TOOLS: _Probe("egress", "GET", {}, path="/api/tools", surface="rest"),
    REST_SYSTEM_ME: _Probe("egress", "GET", {}, path="/api/system/me", surface="rest"),
    REST_CONFIG: _Probe("egress", "GET", {}, path="/api/config", surface="rest"),
    # Another session than the probes' own, suspended and then lifted.
    REST_SESSION_SUSPEND: _Probe("egress", "POST", {}, path="/api/sessions/s-other/suspend", surface="rest"),
    REST_SESSION_LIFT: _Probe("egress", "DELETE", {}, path="/api/sessions/s-other/suspend", surface="rest"),
    REST_PINS: _Probe("egress", "GET", {}, path="/api/admin/tools/pins?tool=add", surface="rest", caller="tenant"),
    REST_WITHDRAW: _Probe(
        "egress", "POST", {}, path=f"/api/admin/tools/{_UPSTREAM}/long_job/withdraw", surface="rest", caller="tenant"
    ),
    REST_RESTORE: _Probe(
        "egress", "POST", {}, path=f"/api/admin/tools/{_UPSTREAM}/long_job/restore", surface="rest", caller="tenant"
    ),
    REST_APPROVALS: _Probe("egress", "GET", {}, path="/api/approvals", surface="rest", caller="tenant"),
    WS_EVENTS: _Probe("egress", "subscribe", {}, path="/api/ws/events", surface="ws", caller="tenant"),
}

#: The paths only a process of their own serves, by the surface the harness drives.
HARNESS = Path(__file__).with_name("_predicate_reachability_harness.py")
HARNESS_PROBES: dict[str, str] = {
    FACADE_INVOKE: "facade",
    STDIO_HANGAR_CALL: "stdio",
    STDIO_FLAT_TOOL_CALL: "stdio_front_door",
}
ALL_PATHS = [*PROBES, *HARNESS_PROBES, *ROUTE_PROBE_NAMES]


# --- the served app ------------------------------------------------------------------


@dataclass
class _Gateway:
    client: Any
    #: The fleet-wide caller's key: `svc:caller`, in `_TENANT`, with a global grant.
    api_key: str
    #: The key of `svc:tenant-caller`, whose grant is confined to `_TENANT`.
    tenant_api_key: str = ""
    #: The ASGI app the client serves, for a stream the client would wait out.
    app: Any = None

    def _mcp_request(self, probe: _Probe, key: str) -> tuple[dict[str, str], bytes]:
        headers = {
            "MCP-Protocol-Version": _MODERN,
            "Mcp-Method": probe.method,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "X-API-Key": key,
            "x-session-id": _SESSION,
        }
        if probe.name is not None:
            headers["Mcp-Name"] = probe.name
        envelope = {
            "io.modelcontextprotocol/protocolVersion": _MODERN,
            "io.modelcontextprotocol/clientInfo": {"name": "reachability-probe", "version": "0"},
            "io.modelcontextprotocol/clientCapabilities": probe.capabilities,
        }
        body = {"jsonrpc": "2.0", "id": 1, "method": probe.method, "params": {**probe.params, "_meta": envelope}}
        return headers, json.dumps(body).encode()

    def send(self, probe: _Probe) -> tuple[int, dict[str, Any]]:
        """Send an MCP request as the fleet-wide caller: the status and the JSON-RPC payload."""
        headers, body = self._mcp_request(probe, self.api_key)
        response = self.client.post(probe.path, headers=headers, content=body)
        return response.status_code, _jsonrpc(response.text)

    def probe(self, probe: _Probe) -> tuple[bool, str]:
        """Send *probe* on its surface, as its caller: whether it was served, and what came back."""
        key = self.tenant_api_key if probe.caller == "tenant" else self.api_key
        if probe.surface == "rest":
            response = self.client.request(probe.method, probe.path, headers={"X-API-Key": key}, json=probe.params)
            return 200 <= response.status_code < 300, f"{response.status_code} {response.text[:300]}"
        if probe.surface == "ws":
            return _event_stream(self.client, probe.path, key)
        if probe.surface == "listen":
            return _listen_acknowledged(self.client, self.app, probe.path, *self._mcp_request(probe, key))
        status, payload = self.send(probe)
        return _served_ok(status, payload), f"{status} {json.dumps(payload)[:300]}"


def _event_stream(client: Any, path: str, key: str) -> tuple[bool, str]:
    """Subscribe to the event stream, publish one event naming the caller's tenant, and receive it."""
    from mcp_hangar.domain.events import ToolInvocationCompleted
    from mcp_hangar.infrastructure.event_bus import get_event_bus

    with client.websocket_connect(path, headers={"X-API-Key": key}) as ws:
        ws.send_json({"type": "subscribe"})
        ack = ws.receive_json()
        # The bus the stream reads. An event naming the caller's tenant is one
        # a tenant-confined subscriber is entitled to, so it must arrive.
        get_event_bus().publish(
            ToolInvocationCompleted(mcp_server_id=_UPSTREAM, tool_name="add", identity_context={"tenant_id": _TENANT})
        )
        event = ws.receive_json()
    served = ack.get("type") == "subscribed" and event.get("event_type") == "ToolInvocationCompleted"
    return served, json.dumps([ack, event])[:300]


def _listen_acknowledged(client: Any, app: Any, path: str, headers: dict[str, str], body: bytes) -> tuple[bool, str]:
    """Open a `subscriptions/listen` stream, read it to its first frame, and hang up.

    The stream never ends on its own, and the test client waits for a response
    to end, so the request is driven on the client's own event loop -- the loop
    the app's lifespan runs on -- with a receive that reports the disconnect
    once the acknowledgement has arrived.
    """
    import anyio

    async def drive() -> tuple[int, bytes]:
        acknowledged = anyio.Event()
        status = 0
        received = b""
        delivered = False

        async def receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await acknowledged.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            nonlocal status, received
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                received += message.get("body", b"")
                if b"data: " in received or not message.get("more_body", False):
                    acknowledged.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in {**headers, "host": "127.0.0.1:8000"}.items()],
            "client": _PEER,
            "server": ("127.0.0.1", 8000),
            "state": {},
        }
        ended = anyio.Event()

        async def serve() -> None:
            try:
                await app(scope, receive, send)
            finally:
                ended.set()

        with anyio.fail_after(15):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(serve)
                await acknowledged.wait()
                # The app sees the disconnect and ends the stream; whatever is
                # left of it is cancelled.
                with anyio.move_on_after(5):
                    await ended.wait()
                tasks.cancel_scope.cancel()
        return status, received

    status, received = client.portal.call(drive)
    payload = _jsonrpc(received.decode(errors="replace"))
    # Served: acknowledged, with every resource it asked for honored, which is
    # what has the subscription decided rather than dropped.
    asked = json.loads(body)["params"]["notifications"].get("resourceSubscriptions") or []
    honored = (payload.get("params") or {}).get("notifications", {}).get("resourceSubscriptions") or []
    served = status == 200 and "error" not in payload and set(asked) <= set(honored)
    return served, f"{status} {json.dumps(payload)[:300]}"


def _jsonrpc(text: str) -> dict[str, Any]:
    """The JSON-RPC payload, from plain JSON or from an SSE `data:` frame."""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    return {"unparsed": text[:300]}


def _served_ok(status: int, payload: dict[str, Any]) -> bool:
    result = payload.get("result")
    return status == 200 and isinstance(result, dict) and not result.get("isError")


@contextmanager
def _served(topology: str, upstream_url: str, workdir: Path) -> Iterator[_Gateway]:
    """The app `serve --http` serves in *topology*, with one upstream and two callers in one tenant."""
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    from mcp_hangar.application.commands import InvokeToolCommand, StartMcpServerCommand
    from mcp_hangar.application.commands.handlers import InvokeToolHandler, StartMcpServerHandler
    from mcp_hangar.application.mcp.tooling import get_tool_authorizer, set_tool_authorizer
    from mcp_hangar.application.queries import register_all_handlers as register_query_handlers
    from mcp_hangar.application.read_models.tool_projection import (
        get_tool_projection_registry,
        reset_tool_projection_registry,
    )
    from mcp_hangar.auth.bootstrap import bootstrap_auth
    from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig
    from mcp_hangar.bootstrap.runtime import create_runtime
    from mcp_hangar.domain.model import McpServer
    from mcp_hangar.domain.policies.egress_l7 import L7Policy, ToolAction
    from mcp_hangar.domain.services import subscription_relay as relay_sink
    from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
    from mcp_hangar.domain.value_objects.tool_digest import DigestEnforcement, ToolDigest
    from mcp_hangar.fastmcp_server import subscription_relay
    from mcp_hangar.fastmcp_server.task_relay_wiring import enable_governed_task_relay
    from mcp_hangar.infrastructure.command_bus import CommandBus
    from mcp_hangar.infrastructure.event_bus import EventBus
    from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
    from mcp_hangar.infrastructure.query_bus import QueryBus
    from mcp_hangar.protocol import is_task_relay_wired, set_task_relay_wired
    from mcp_hangar.server.api import create_api_router
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.bootstrap.components import build_approval_service
    from mcp_hangar.server.bootstrap.event_handlers import init_event_handlers
    from mcp_hangar.server.config import _init_interceptors_from_config, _init_tenant_limits_from_config
    from mcp_hangar.server.context import init_context, reset_context
    from mcp_hangar.server.lifecycle import mcp_app_for_serving
    from mcp_hangar.server.tools.batch import configure_interceptors
    from mcp_hangar.server.tools.batch.tenant_admission import configure_tenant_limits

    authorizer_before, relay_before = get_tool_authorizer(), is_task_relay_wired()
    reset_context()
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    # The build must install the tool authorizer itself. One left behind by an
    # earlier test would hide a build that no longer does.
    set_tool_authorizer(None)
    runtime = None
    try:
        get_tool_access_resolver().set_topology_mode(topology)
        components = bootstrap_auth(
            AuthConfig(
                enabled=True,
                allow_anonymous=False,
                api_key=ApiKeyAuthConfig(enabled=True),
                storage=StorageConfig(driver="memory", path=str(workdir / "auth.db")),
                role_assignments=[
                    RoleAssignment(principal="svc:caller", role="admin", scope="global"),
                    # The same role, held only within the tenant: what a
                    # `tenant_aware` REST route and the event stream confine to.
                    RoleAssignment(principal="svc:tenant-caller", role="admin", scope=f"tenant:{_TENANT}"),
                ],
            )
        )
        api_key = components.api_key_store.create_key(principal_id="svc:caller", name="probe", tenant_id=_TENANT)
        tenant_api_key = components.api_key_store.create_key(
            principal_id="svc:tenant-caller", name="tenant-probe", tenant_id=_TENANT
        )

        runtime = create_runtime(
            event_bus=EventBus(event_store=InMemoryEventStore()), command_bus=CommandBus(), query_bus=QueryBus()
        )
        runtime.command_bus.register(
            StartMcpServerCommand, StartMcpServerHandler(runtime.repository, runtime.event_bus)
        )
        runtime.command_bus.register(InvokeToolCommand, InvokeToolHandler(runtime.repository, runtime.event_bus))
        register_query_handlers(runtime.query_bus, runtime.repository, event_store=runtime.event_bus.event_store)
        runtime.repository.add(
            _UPSTREAM,
            McpServer(
                mcp_server_id=_UPSTREAM,
                mode="remote",
                endpoint=upstream_url,
                # Allow-all, so it refuses nothing, but it is evaluated.
                l7_policy=L7Policy(default_action=ToolAction.ALLOW),
            ),
        )
        context = init_context(runtime)
        context.auth_components = components
        # The approval gate `bootstrap()` builds (`build_approval_service`), so
        # the approval routes serve. Nothing is on an approval list, so it holds no call.
        context.approval_gate = build_approval_service({}, runtime.event_bus)
        init_event_handlers(runtime)
        # One configured interceptor, read from config the way
        # `load_configuration` reads it, so every invoke path must run it
        # (#1425). The cap is far above any probe, so it refuses nothing.
        _init_interceptors_from_config(
            {"interceptors": {"validators": [{"type": "payload_size", "max_bytes": 1_000_000}]}}
        )
        # A budget for the caller's tenant, read from config the same way
        # (#1445), so each invoke path takes and gives back a real slot. It is
        # far above what the probes spend, so it refuses nothing.
        _init_tenant_limits_from_config(
            {"execution": {"tenant_limits": {"tenant-a": {"max_concurrency": 100, "rps": 1000, "burst": 1000}}}}
        )

        server = build_serving_mcp_server()
        enable_governed_task_relay(server, relay_tasks_enabled=True)
        # As `run_http` assembles it: the REST API under `/api` in an aux app,
        # chosen by path prefix ahead of the MCP app, the whole of it behind
        # the auth enforcement.
        mcp_app = mcp_app_for_serving(server)
        aux_app = Starlette(routes=[Mount("/api", app=create_api_router(auth_components=components))])

        async def combined_app(scope: Any, receive: Any, send: Any) -> None:
            path = scope.get("path", "")
            if scope["type"] in ("http", "websocket") and (path == "/api" or path.startswith("/api/")):
                await aux_app(scope, receive, send)
                return
            await mcp_app(scope, receive, send)

        app = create_auth_enforced_app(combined_app, components)

        # Started the way the front door's boot warm-up starts it, through the
        # command bus, so `McpServerStarted` populates the catalogue that the
        # pin and withdrawal checks read. In `egress` the first call would.
        runtime.command_bus.send(StartMcpServerCommand(mcp_server_id=_UPSTREAM))
        # A pin that does not match, under `warn`: the digest check runs and
        # the call still goes through to the gates after it.
        pins = get_tool_projection_registry()
        pins.set_config_pin(_UPSTREAM, "add", None, ToolDigest(tool_name="add", sha256="0" * 64))
        pins.set_digest_enforcement(_UPSTREAM, DigestEnforcement.WARN)

        with TestClient(app, base_url=_BASE_URL, client=_PEER) as client:
            yield _Gateway(client=client, api_key=api_key, tenant_api_key=tenant_api_key, app=app)
    finally:
        if runtime is not None:
            for mcp_server in runtime.repository.get_all().values():
                mcp_server.shutdown()
        configure_interceptors(None)
        configure_tenant_limits({})
        # The front door's listen relay keeps the loop of the stream it served,
        # which closes with the client, and its sink outlives the app.
        subscription_relay._upstream_refs.clear()
        subscription_relay._bus._listeners.clear()
        subscription_relay._loop = None
        relay_sink.clear_sink()
        reset_context()
        reset_tool_access_resolver()
        reset_tool_projection_registry()
        set_tool_authorizer(authorizer_before)
        set_task_relay_wired(relay_before)


@dataclass(frozen=True)
class _Outcome:
    served: bool
    #: What came back, for a failure message.
    detail: str
    calls: frozenset[tuple[str, Any]]
    #: Each registered predicate's scope arguments on this path: (predicate, role, (parameter, value)).
    scopes: frozenset[tuple[str, str, Any]] = frozenset()


def _start_harness(surface: str, workdir: Path, *, mutate: bool = False) -> tuple[subprocess.Popen[str], Path]:
    out = workdir / "run.json"
    command = [sys.executable, str(HARNESS), surface, str(out), *(["--mutate"] if mutate else [])]
    return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True), out


def _harness_report(started: tuple[subprocess.Popen[str], Path]) -> dict[str, Any]:
    process, out = started
    _out, err = process.communicate(timeout=120)
    assert process.returncode == 0 and out.exists(), f"the harness exited {process.returncode}:\n{err[-4000:]}"
    report = json.loads(out.read_text())
    # It must have imported this checkout, not an installed copy.
    assert report["hangar"].startswith(str(Path(__file__).resolve().parents[2])), report["hangar"]
    return report


def _reported_outcome(report: dict[str, Any]) -> _Outcome:
    calls, scopes = from_json(report)
    return _Outcome(report["served"], report["detail"], calls, scopes)


def _harness_outcome(started: tuple[subprocess.Popen[str], Path]) -> _Outcome:
    return _reported_outcome(_harness_report(started))


def _route_outcomes(started: tuple[subprocess.Popen[str], Path]) -> dict[str, _Outcome]:
    """The `rest` surface's outcome for each route it probed."""
    return {name: _reported_outcome(route) for name, route in _harness_report(started)["routes"].items()}


def _probe_all(spy: Spy, upstream_url: str, tmp_path_factory: pytest.TempPathFactory) -> dict[str, _Outcome]:
    """Send every in-process probe once, recording which predicates each one ran."""
    from mcp_hangar.fastmcp_server import asgi

    outcomes: dict[str, _Outcome] = {}
    with pytest.MonkeyPatch.context() as env:
        # The default trusted proxies, loopback among them, however an earlier
        # test left the variable or its cache.
        env.delenv("MCP_TRUSTED_PROXIES", raising=False)
        asgi._forwarded_session_extractor.cache_clear()
        try:
            for topology in ("egress", "front_door"):
                with _served(topology, upstream_url, tmp_path_factory.mktemp(topology)) as gateway:
                    minted = gateway.send(_MINT_TASK[topology])
                    assert _served_ok(*minted), f"minting the task for tasks/* failed on {topology}: {minted}"
                    for path, probe in PROBES.items():
                        if probe.topology != topology:
                            continue
                        with spy.recording() as (calls, scopes):
                            served, detail = gateway.probe(probe)
                        outcomes[path] = _Outcome(served, detail, frozenset(calls), frozenset(scopes))
        finally:
            asgi._forwarded_session_extractor.cache_clear()
    return outcomes


@pytest.fixture(scope="module")
def reached(tmp_path_factory: pytest.TempPathFactory) -> dict[str, _Outcome]:
    """Every path's outcome: the in-process probes, and each harness run, started first so they overlap."""
    started = {
        path: _start_harness(surface, tmp_path_factory.mktemp(surface)) for path, surface in HARNESS_PROBES.items()
    }
    routes = _start_harness("rest", tmp_path_factory.mktemp("rest"))
    spy = Spy(PREDICATES)
    with _upstream() as upstream_url, spy.installed():
        outcomes = _probe_all(spy, upstream_url, tmp_path_factory)
    for path, run in started.items():
        outcomes[path] = _harness_outcome(run)
    return {**outcomes, **_route_outcomes(routes)}


# --- the tests ---------------------------------------------------------------------------


class TestEveryProbeIsServed:
    """A refused probe stops before the predicates after the refusal.

    Its failure would read as "unreachable" when the probe is at fault, so each
    one must first be served.
    """

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_the_request_is_served(self, reached: dict[str, _Outcome], path: str) -> None:
        outcome = reached[path]

        assert outcome.served, f"the probe for {path} was not served, so it proves nothing: {outcome.detail}"


_DECLARED = [
    pytest.param(predicate, path, id=f"{predicate.name} on {path}")
    for predicate in PREDICATES
    for path in predicate.paths
]


class TestEveryPredicateRunsOnItsPaths:
    """The half that keeps these checks real. A check nothing calls is not a check."""

    @pytest.mark.parametrize(("predicate", "path"), _DECLARED)
    def test_it_runs(self, reached: dict[str, _Outcome], predicate: Predicate, path: str) -> None:
        expected = predicate.paths[path]
        ran_as = {value for name, value in reached[path].calls if name == predicate.name}

        where = f" with {predicate.by}={expected!r}" if expected is not None else ""
        assert (expected in ran_as) if expected is not None else ran_as, (
            f"{predicate.name} did not run{where} on {path}"
            + (f" (it ran with {predicate.by} in {sorted(map(str, ran_as))})" if ran_as else "")
            + ". Either the call was removed from that path, or the table in _security_predicates.py is wrong."
        )


#: What each scope role may hold on a probe: the caller's tenant (or none), the
#: probes' one upstream (or none). The probes' tenant and server ids differ, so a
#: value in the wrong slot cannot pass for the right one. Every caller, on every
#: surface, is in `_TENANT`.
_ALLOWED = {"tenant": {None, _TENANT}, "server": {None, _UPSTREAM}}


def scope_violations(outcome: _Outcome) -> list[str]:
    """The scope arguments on a path that hold what their slot must not: a server in a tenant slot, and the like."""
    return sorted(
        f"{predicate}({param}={value!r}) is a {role} slot"
        for predicate, role, (param, value) in outcome.scopes
        if value not in _ALLOWED[role]
    )


class TestEveryPredicateIsAskedAboutTheRequestsOwnScope:
    """Running is not enough: a check reached with the wrong key decides about something else (#1786)."""

    @pytest.mark.parametrize("path", ALL_PATHS)
    def test_the_scope_arguments_are_the_callers_and_the_targets(self, reached: dict[str, _Outcome], path: str) -> None:
        assert scope_violations(reached[path]) == []

    def test_a_scope_slot_is_actually_observed_on_every_path_that_declares_one(
        self, reached: dict[str, _Outcome]
    ) -> None:
        """A recorder that saw nothing would make the check above pass for the wrong reason."""
        for predicate in PREDICATES:
            slots = {*predicate.scope.items(), *([("return", predicate.returns)] if predicate.returns else [])}
            if not slots:
                continue
            for path in predicate.paths:
                seen = {(p, role) for name, role, (p, _v) in reached[path].scopes if name == predicate.name}
                assert seen == slots, (predicate.name, path, seen)

    def test_the_tenant_slot_holds_the_callers_tenant_where_the_call_has_one(
        self, reached: dict[str, _Outcome]
    ) -> None:
        held = {
            value
            for outcome in reached.values()
            for _name, role, (_param, value) in outcome.scopes
            if role == "tenant" and value is not None
        }
        assert held == {_TENANT}, "no path asked about the caller's tenant, so the tenant check proves nothing"


#: Per surface: how it is mis-scoped, and the predicate the gate must then name.
#: `is_allowed` is asked with the server id as the member, where the caller's
#: tenant goes; the event stream asks whether an event is visible to the server
#: id instead of to the subscriber's tenant (and lets it through); the route
#: guard records the server id as the tenant a confined grant reaches, and so
#: does the resolve handler, in the `rest` harness.
_MUTATIONS: dict[str, tuple[str, str]] = {
    HANGAR_CALL: ("is_allowed", "is_allowed"),
    SUBSCRIPTIONS_LISTEN: ("is_allowed", "is_allowed"),
    FACADE_INVOKE: ("is_allowed", "is_allowed"),
    STDIO_HANGAR_CALL: ("is_allowed", "is_allowed"),
    STDIO_FLAT_TOOL_CALL: ("is_allowed", "is_allowed"),
    WS_EVENTS: ("visible_to_tenant", "visible_to_tenant"),
    REST_TOOL_HISTORY: ("grant_scope", "confined_tenant"),
    REST_APPROVAL_RESOLVE: ("rest", "ResolveApprovalHandler._within_grant"),
}


def _mutate(env: pytest.MonkeyPatch, mutation: str) -> None:
    if mutation == "is_allowed":
        from mcp_hangar.domain.services import _policy_merge

        is_allowed = _policy_merge.is_allowed

        def server_in_the_tenant_slot(resolver, mcp_server_id, name, **kwargs):  # type: ignore[no-untyped-def]
            return is_allowed(resolver, mcp_server_id, name, **{**kwargs, "member_id": mcp_server_id})

        env.setattr(_policy_merge, "is_allowed", server_in_the_tenant_slot)
    elif mutation == "visible_to_tenant":
        from mcp_hangar.server.api.ws import events

        visible = events.visible_to_tenant
        env.setattr(events, "visible_to_tenant", lambda event, _tenant_id: visible(event, _UPSTREAM) or True)
    elif mutation == "grant_scope":
        from mcp_hangar.domain.contracts.authorization import GrantScope
        from mcp_hangar.server.api import middleware

        record = middleware.record_grant_scope

        def server_as_the_tenant(scope: Any, grant: GrantScope) -> None:
            record(scope, GrantScope(confined=True, tenant_id=_UPSTREAM) if grant.confined else grant)

        env.setattr(middleware, "record_grant_scope", server_as_the_tenant)
    else:
        raise AssertionError(mutation)


def _mutated(path: str, workdir: Path) -> _Outcome:
    """*path*'s one probe, sent with its mutation applied."""
    mutation, _predicate = _MUTATIONS[path]
    if path in ROUTE_PROBE_NAMES:
        return _route_outcomes(_start_harness("rest", workdir, mutate=True))[path]
    if path in HARNESS_PROBES:
        return _harness_outcome(_start_harness(HARNESS_PROBES[path], workdir, mutate=True))
    from mcp_hangar.fastmcp_server import asgi

    probe = PROBES[path]
    spy = Spy(PREDICATES)
    with _upstream() as upstream_url, pytest.MonkeyPatch.context() as env, spy.installed():
        env.delenv("MCP_TRUSTED_PROXIES", raising=False)
        _mutate(env, mutation)
        asgi._forwarded_session_extractor.cache_clear()
        try:
            with _served(probe.topology, upstream_url, workdir) as gateway:
                with spy.recording() as (calls, scopes):
                    served, detail = gateway.probe(probe)
        finally:
            asgi._forwarded_session_extractor.cache_clear()
    return _Outcome(served, detail, frozenset(calls), frozenset(scopes))


class TestAMisScopedPredicateIsCaught:
    """The gate must fail when a surface asks a predicate about the wrong scope (mutation test).

    One mutation per surface, on a predicate that surface asks with a scope: a
    server id where the tenant goes.
    """

    @pytest.mark.parametrize("path", list(_MUTATIONS))
    def test_the_mutation_is_detected(self, tmp_path_factory: pytest.TempPathFactory, path: str) -> None:
        _mutation, predicate = _MUTATIONS[path]

        outcome = _mutated(path, tmp_path_factory.mktemp("mutated"))

        violations = scope_violations(outcome)
        assert [v for v in violations if v.startswith(f"{predicate}(")], (
            f"a server id in {predicate}'s tenant slot on {path} was not noticed (violations: {violations})"
        )


def rule_keys(rule: RouteRule) -> set[str]:
    """A rule's name per method: `POST,PUT /x` is two things to probe, and `ANY /x` is one."""
    methods, template = rule.template.split(" ", 1)
    return {f"{method} {template}" for method in methods.split(",")}


def _key_of(rule: RouteRule, method: str) -> str:
    return f"{'ANY' if rule.methods is None else method} {rule.template.split(' ', 1)[1]}"


def _probed_requests() -> set[tuple[str, str]]:
    """Every REST or WebSocket request the gate sends, as (method, path below `/api`, query aside)."""
    in_process = {
        ("WEBSOCKET" if probe.surface == "ws" else probe.method, probe.path.removeprefix("/api").split("?", 1)[0])
        for probe in PROBES.values()
        if probe.surface in ("rest", "ws")
    }
    return in_process | {(probe.method, probe.path.split("?", 1)[0]) for probe in ROUTE_PROBES}


def unprobed_rules(rules: Iterable[RouteRule], requests: set[tuple[str, str]], exempt: Mapping[str, str]) -> set[str]:
    """Each rule, per method, that no request exercises and no exemption names."""
    probed = set()
    for method, path in requests:
        rule = next((r for r in rules if r.matches(method, path)), None)
        if rule is not None:
            probed.add(_key_of(rule, method))
    return {key for rule in rules for key in rule_keys(rule)} - probed - set(exempt)


def _served_routes(routes: Iterable[Any], prefix: str = "") -> Iterator[tuple[str, str]]:
    """Every (method, path template) the REST API serves, in the order the router tries them."""
    for route in routes:
        if isinstance(route, Mount):
            yield from _served_routes(route.routes, prefix + route.path)
        elif isinstance(route, Route):
            for method in sorted(route.methods or ()):
                if method != "HEAD":
                    yield method, (prefix + route.path).rstrip("/")
        elif isinstance(route, WebSocketRoute):
            yield "WEBSOCKET", prefix + route.path


def _concrete(path: str) -> str:
    """*path* as a request: each `{placeholder}`, a route's or a probe's, stands for one segment."""
    return re.sub(r"\{[^}]+\}", "x", path)


def unprobed_served_routes(served: Iterable[tuple[str, str]], requests: set[tuple[str, str]]) -> set[str]:
    """Each served route no request reaches, which sees past a rule that covers a subtree (`/auth/**`)."""
    routes = [(method, path, compile_path(path)[0]) for method, path in served]
    reached = set()
    for sent, path in requests:
        # The first match wins, as in the router.
        matched = next((p for m, p, regex in routes if m == sent and regex.fullmatch(_concrete(path))), None)
        reached.add((sent, matched))
    return {f"{method} {path}" for method, path, _regex in routes if (method, path) not in reached}


class TestEveryRouteHasAProbe:
    """A new route goes unprobed only by saying why (#2162).

    The rule table is checked per method, and the served routes too, because one
    rule (`/auth/**`) covers a whole subtree.
    """

    def test_every_rule_is_probed_or_exempt(self) -> None:
        assert unprobed_rules(ROUTE_PERMISSIONS, _probed_requests(), EXEMPT_ROUTES) == set()

    def test_every_served_route_is_probed_or_exempt(self) -> None:
        served = list(_served_routes(create_api_router().routes))
        exempt = [
            f"{method} {path}"
            for method, path in served
            if (rule := rule_of(method, _concrete(path))) and _key_of(rule, method) in EXEMPT_ROUTES
        ]

        assert unprobed_served_routes(served, _probed_requests()) - set(exempt) == set()

    def test_a_new_rule_with_no_probe_fails(self) -> None:
        rule = RouteRule(re.compile("/probe-me"), frozenset({"POST"}), ("x", "y"), "POST /probe-me")

        unprobed = unprobed_rules((*ROUTE_PERMISSIONS, rule), _probed_requests(), EXEMPT_ROUTES)

        assert "POST /probe-me" in unprobed

    def test_a_new_route_under_a_covering_rule_with_no_probe_fails(self) -> None:
        served = [("GET", "/auth/keys"), ("POST", "/auth/brand-new")]

        assert unprobed_served_routes(served, _probed_requests()) == {"POST /auth/brand-new"}

    def test_a_probe_is_governed_by_a_rule(self) -> None:
        assert [p.name for p in ROUTE_PROBES if rule_of(p.method, p.path) is None] == []

    def test_an_exemption_names_a_rule_and_gives_a_reason(self) -> None:
        keys = {key for rule in ROUTE_PERMISSIONS for key in rule_keys(rule)}

        assert [k for k, reason in EXEMPT_ROUTES.items() if k not in keys or not reason.strip()] == []

    def test_an_exemption_is_not_also_probed(self) -> None:
        unprobed = unprobed_rules(ROUTE_PERMISSIONS, _probed_requests(), {})

        assert set(EXEMPT_ROUTES) <= unprobed


class TestTheTableAndTheProbesAgree:
    def test_every_declared_path_has_a_probe(self) -> None:
        declared = {path for predicate in PREDICATES for path in predicate.paths}

        assert declared <= set(ALL_PATHS), f"paths with no probe: {sorted(declared - set(ALL_PATHS))}"

    def test_every_probe_is_a_declared_path(self) -> None:
        declared = {path for predicate in PREDICATES for path in predicate.paths}

        assert set(ALL_PATHS) <= declared, f"probes no predicate declares: {sorted(set(ALL_PATHS) - declared)}"

    def test_each_predicate_is_registered_once(self) -> None:
        names = [predicate.name for predicate in PREDICATES]

        assert len(names) == len(set(names)), f"registered twice: {sorted({n for n in names if names.count(n) > 1})}"

    def test_by_names_a_parameter_of_the_predicate(self) -> None:
        wrong = [
            predicate.name
            for predicate in PREDICATES
            if predicate.by is not None and predicate.by not in inspect.signature(predicate.function).parameters
        ]

        assert not wrong, f"`by` names no parameter of: {wrong}"

    def test_a_scope_slot_names_a_parameter_of_the_predicate(self) -> None:
        wrong = [
            (predicate.name, param)
            for predicate in PREDICATES
            for param in predicate.scope
            if param.split(".")[0] not in inspect.signature(predicate.function).parameters
        ]

        assert not wrong, f"`scope` names no parameter of: {wrong}"

    def test_every_mutated_surface_is_a_probed_path(self) -> None:
        assert set(_MUTATIONS) <= set(ALL_PATHS)
