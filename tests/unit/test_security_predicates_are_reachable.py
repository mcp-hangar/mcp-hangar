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
API key, from loopback. Loopback is a trusted proxy by default, so the
``x-session-id`` every request carries gives the suspension check a session to
look up. The one upstream is an in-process HTTP MCP server. No handler, gate or
predicate is patched: ``test_tool_permissions_cover_the_surface.py`` is the
model, and the lesson of #596 and #963-#965 is that a surface assembled by hand
can agree with a table while production disagrees.

The spy is a profile hook matched on each predicate's code object. It sees a
call however the caller reached the function: an import alias, the executor's
tuple of gates, or a closure. It also sees calls on every thread, which matters
because ``hangar_call`` runs its calls on a worker pool. It is installed before
the client starts, so every thread the served app starts inherits it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
from pathlib import Path
import sys
import threading
from types import FrameType
from typing import Any

import pytest

# First: `server.bootstrap` is the import order production uses (#894).
from mcp_hangar.server.bootstrap import build_serving_mcp_server
from mcp_hangar.tasks_wire import EXTENSION_ID
from tests.unit._security_predicates import (
    COMPLETION,
    FLAT_TOOL_CALL,
    FRONT_DOOR_MANAGEMENT_TOOL,
    FRONT_DOOR_TASKS_CANCEL,
    FRONT_DOOR_TASKS_GET,
    FRONT_DOOR_TASKS_UPDATE,
    HANGAR_CALL,
    MANAGEMENT_TOOL,
    PREDICATES,
    PROMPTS_GET,
    PROMPTS_LIST,
    RESOURCE_TEMPLATES_LIST,
    RESOURCES_LIST,
    RESOURCES_READ,
    TASKS_CANCEL,
    TASKS_GET,
    TASKS_UPDATE,
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
    method: str
    params: dict[str, Any]
    #: The SEP-2243 `Mcp-Name` routing header, where the method carries a name.
    name: str | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)


def _hangar_call(tool: str) -> dict[str, Any]:
    return {
        "name": "hangar_call",
        "arguments": {"calls": [{"mcp_server": _UPSTREAM, "tool": tool, "arguments": {"a": 1}}]},
    }


_TASKS = {"extensions": {EXTENSION_ID: {}}}
_READ_URI = f"hangar://{_UPSTREAM}/doc://one"

#: Sent before each topology's probes, unrecorded: mints the task `tasks/*` act
#: on. On the front door the flat call mints it, so the front door's `tasks/*`
#: probes act on a task that path created (#1394).
_MINT_TASK = {
    "egress": _Probe("egress", "tools/call", _hangar_call("long_job"), name="hangar_call"),
    "front_door": _Probe("front_door", "tools/call", {"name": "long_job", "arguments": {}}, name="long_job"),
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
}


# --- the spy -----------------------------------------------------------------------


class _Spy:
    """Which registered predicates ran, with the argument naming the call site."""

    def __init__(self, predicates: tuple[Predicate, ...]) -> None:
        # Keyed by identity. The functions are module attributes, so their code
        # objects, and so these ids, live as long as the process.
        self._watched = {id(p.function.__code__): p for p in predicates}
        self._lock = threading.Lock()
        self._recording = False
        self._calls: set[tuple[str, Any]] = set()

    def _hook(self, frame: FrameType, event: str, _arg: Any) -> None:
        if event != "call" or not self._recording:
            return
        predicate = self._watched.get(id(frame.f_code))
        if predicate is None:
            return
        value = frame.f_locals.get(predicate.by) if predicate.by else None
        with self._lock:
            self._calls.add((predicate.name, value))

    @contextmanager
    def installed(self) -> Iterator[None]:
        """Hook this thread and every thread started while installed."""
        previous = sys.getprofile(), threading.getprofile()
        sys.setprofile(self._hook)
        threading.setprofile(self._hook)
        try:
            yield
        finally:
            sys.setprofile(previous[0])
            threading.setprofile(previous[1])

    @contextmanager
    def recording(self) -> Iterator[set[tuple[str, Any]]]:
        calls: set[tuple[str, Any]] = set()
        with self._lock:
            self._calls = calls
            self._recording = True
        try:
            yield calls
        finally:
            with self._lock:
                self._recording = False


# --- the served app ------------------------------------------------------------------


@dataclass
class _Gateway:
    client: Any
    api_key: str

    def send(self, probe: _Probe) -> tuple[int, dict[str, Any]]:
        headers = {
            "MCP-Protocol-Version": _MODERN,
            "Mcp-Method": probe.method,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
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
        response = self.client.post("/mcp", headers=headers, content=json.dumps(body))
        return response.status_code, _jsonrpc(response.text)


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
    """The app `serve --http` serves in *topology*, with one upstream and one caller."""
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
    from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
    from mcp_hangar.domain.value_objects.tool_digest import DigestEnforcement, ToolDigest
    from mcp_hangar.fastmcp_server.task_relay_wiring import enable_governed_task_relay
    from mcp_hangar.infrastructure.command_bus import CommandBus
    from mcp_hangar.infrastructure.event_bus import EventBus
    from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
    from mcp_hangar.infrastructure.query_bus import QueryBus
    from mcp_hangar.protocol import is_task_relay_wired, set_task_relay_wired
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.bootstrap.event_handlers import init_event_handlers
    from mcp_hangar.server.config import _init_interceptors_from_config
    from mcp_hangar.server.context import init_context, reset_context
    from mcp_hangar.server.lifecycle import mcp_app_for_serving
    from mcp_hangar.server.tools.batch import configure_interceptors

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
                role_assignments=[RoleAssignment(principal="svc:caller", role="admin", scope="global")],
            )
        )
        api_key = components.api_key_store.create_key(principal_id="svc:caller", name="probe", tenant_id="tenant-a")

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
        init_event_handlers(runtime)
        # One configured interceptor, read from config the way
        # `load_configuration` reads it, so every invoke path must run it
        # (#1425). The cap is far above any probe, so it refuses nothing.
        _init_interceptors_from_config(
            {"interceptors": {"validators": [{"type": "payload_size", "max_bytes": 1_000_000}]}}
        )

        server = build_serving_mcp_server()
        enable_governed_task_relay(server, relay_tasks_enabled=True)
        app = create_auth_enforced_app(mcp_app_for_serving(server), components)

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
            yield _Gateway(client=client, api_key=api_key)
    finally:
        if runtime is not None:
            for mcp_server in runtime.repository.get_all().values():
                mcp_server.shutdown()
        configure_interceptors(None)
        reset_context()
        reset_tool_access_resolver()
        reset_tool_projection_registry()
        set_tool_authorizer(authorizer_before)
        set_task_relay_wired(relay_before)


@dataclass(frozen=True)
class _Outcome:
    status: int
    payload: dict[str, Any]
    calls: frozenset[tuple[str, Any]]


@pytest.fixture(scope="module")
def reached(tmp_path_factory: pytest.TempPathFactory) -> dict[str, _Outcome]:
    """Send every probe once, recording which predicates each one ran."""
    from mcp_hangar.fastmcp_server import asgi

    spy = _Spy(PREDICATES)
    outcomes: dict[str, _Outcome] = {}
    with _upstream() as upstream_url, pytest.MonkeyPatch.context() as env, spy.installed():
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
                        with spy.recording() as calls:
                            status, payload = gateway.send(probe)
                        outcomes[path] = _Outcome(status, payload, frozenset(calls))
        finally:
            asgi._forwarded_session_extractor.cache_clear()
    return outcomes


# --- the tests ---------------------------------------------------------------------------


class TestEveryProbeIsServed:
    """A refused probe stops before the predicates after the refusal.

    Its failure would read as "unreachable" when the probe is at fault, so each
    one must first be served.
    """

    @pytest.mark.parametrize("path", list(PROBES))
    def test_the_request_is_served(self, reached: dict[str, _Outcome], path: str) -> None:
        outcome = reached[path]

        assert _served_ok(outcome.status, outcome.payload), (
            f"the probe for {path} was not served, so it proves nothing: {outcome.status} {outcome.payload}"
        )


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


class TestTheTableAndTheProbesAgree:
    def test_every_declared_path_has_a_probe(self) -> None:
        declared = {path for predicate in PREDICATES for path in predicate.paths}

        assert declared <= set(PROBES), f"paths with no probe: {sorted(declared - set(PROBES))}"

    def test_every_probe_is_a_declared_path(self) -> None:
        declared = {path for predicate in PREDICATES for path in predicate.paths}

        assert set(PROBES) <= declared, f"probes no predicate declares: {sorted(set(PROBES) - declared)}"

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
