"""Bootstrap Hangar, drive every canary through the served app, and dump every sink (#1535).

Run as a script, in its own interpreter, by ``test_telemetry_canaries.py``:
``python _canary_harness.py <surface> <out.json> <otlp endpoint>``. Not
collected by pytest.

A separate process for the reason ``_trace_harness.py`` gives: the tracer and
logger providers are registered once per process, and what the bootstrap does
depends on whether anything registered one first. Here nothing has.

What runs is production:

- ``setup_logging(level="INFO", json_format=True, log_file=...)``, the call
  ``serve`` makes, at the level it defaults to. Every structlog and stdlib line
  reaches the root handlers, so the log file is the whole log, not the part a
  capture fixture happens to see;
- ``bootstrap()`` from a config file: tracing on with an OTLP endpoint (nothing
  listens on it, so exports stay local), so the owned tracer and audit log
  pipelines are built, and the CEF compliance exporter from
  ``MCP_COMPLIANCE_FORMAT``, which the parent sets;
- the app ``serve --http`` serves -- ``/mcp`` from ``mcp_app_for_serving`` beside
  the REST API mounted at ``/api`` -- under starlette's ``TestClient``;
- stateless ``tools/call`` POSTs to two upstreams serving the same tools from
  ``_canary_upstream.py``: one over stdio, one over HTTP;
- the approval gate ``bootstrap()`` wires, answered by a thread that denies each
  hold with the approver canary as its reason, through the service call the
  REST resolve route makes.

Surfaces: ``hangar_call`` is the default topology, each call a ``hangar_call``;
``front_door`` is ``tool_access.mode: front_door``, warmed as ``run_http`` warms
it, each call the upstream tool's own flat ``tools/call``.

Added, and only as observers: in-memory exporters on the providers the
bootstrap registered, the three compliance formats one process cannot select at
once (LEEF, JSON lines, syslog) subscribed exactly as the bootstrap subscribes
CEF, and one ``/api/ws/events`` subscriber.

Every request carries the baggage and header canaries, so they cross the
boundary on every scenario, both transports and both surfaces.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import _canary_upstream as upstream

UPSTREAM = Path(upstream.__file__).resolve()
BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "canary-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
canary = upstream.canary
prefix = upstream.prefix
HEADERS = {
    "MCP-Protocol-Version": MODERN_VERSION,
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

#: Who makes the calls. The front door serves only a caller with a tenant.
CALLER = "svc:canary-caller"
TENANT = "tenant-canary"

#: transport -> the server that reaches its upstream over it.
SERVERS = {"stdio": "canary-stdio", "http": "canary-http"}

#: scenario -> tool. Each runs once per transport, its tool under the
#: transport's prefix (``stdio-note``).
SCENARIOS = {
    "argument": "note",
    "result": "result_text",
    "is_error": "is_error",
    "rpc_error": "rpc_error",
    "approval": "guarded",
}


def arguments(tool: str, transport: str) -> dict[str, Any]:
    if tool != "note":
        return {}
    return {"note": canary("argument", transport), "api_token": canary("secret_argument", transport)}


#: The event types ``/api/ws/events`` is asked for, and read back from the store.
WS_EVENT_TYPES = (
    "ToolInvocationRequested",
    "ToolInvocationCompleted",
    "ToolInvocationFailed",
    "ToolApprovalRequested",
    "ToolApprovalGranted",
    "ToolApprovalDenied",
    "ToolApprovalExpired",
)


class HttpUpstream(BaseHTTPRequestHandler):
    """``_canary_upstream.answer`` over HTTP, recording what each request carried."""

    seen: ClassVar[list[dict[str, Any]]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 -- http.server's handler name
        # No server-to-client stream: 405 is how an upstream says so.
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.seen.append({**upstream.seen(request), "headers": dict(self.headers.items())})
        response = upstream.answer(request, "http")
        body = b"" if response is None else json.dumps(response).encode()
        self.send_response(202 if response is None else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _config(surface: str, endpoint: str, http_upstream: str, stdio_record: Path) -> dict[str, Any]:
    def guarded(transport: str) -> dict[str, Any]:
        return {"approval_list": [prefix(transport) + "guarded"], "approval_timeout_seconds": 10}

    config: dict[str, Any] = {
        "mcp_servers": {
            SERVERS["stdio"]: {
                "mode": "subprocess",
                "command": [sys.executable, str(UPSTREAM)],
                "env": {"CANARY_UPSTREAM_RECORD": str(stdio_record)},
                "tools": guarded("stdio"),
            },
            SERVERS["http"]: {"mode": "remote", "endpoint": http_upstream, "tools": guarded("http")},
        },
        "observability": {"tracing": {"otlp_endpoint": endpoint, "enabled": True}},
        "auth": {
            "enabled": True,
            "allow_anonymous": False,
            "api_key": {"enabled": True, "header_name": "X-API-Key"},
            "storage": {"driver": "memory"},
        },
    }
    if surface == "front_door":
        config["tool_access"] = {"mode": "front_door"}
    return config


def _deny_every_hold(gate: Any) -> None:
    """Be the approver: deny each pending hold, giving the approver canary of its transport as the reason."""
    transport_of = {server: transport for transport, server in SERVERS.items()}
    while True:
        try:
            for request in asyncio.run(gate._repository.list_pending()):
                asyncio.run(
                    gate.resolve(
                        request.approval_id,
                        approved=False,
                        decided_by="approver",
                        reason=canary("approver_reason", transport_of[request.mcp_server_id]),
                    )
                )
        except Exception:  # noqa: BLE001 -- a harness thread must never take the run down
            pass
        time.sleep(0.05)


def _subscribe_other_compliance_formats(runtime: Any, directory: Path) -> None:
    """LEEF, JSON lines and syslog, each wired the way ``init_event_handlers`` wires the configured one."""
    from mcp_hangar.application.event_handlers.audit_event_handler import OTLPAuditEventHandler
    from mcp_hangar.domain.contracts.cost import NullCostAttributor
    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import McpServerStateChanged, ToolInvocationCompleted, ToolInvocationFailed
    from mcp_hangar.server.bootstrap.event_handlers import _create_compliance_exporter

    for name in ("leef", "jsonlines", "syslog"):
        exporter = _create_compliance_exporter(name, str(directory / f"{name}.log"))
        if exporter is None:
            raise SystemExit(f"no {name} compliance exporter")
        handler = OTLPAuditEventHandler(
            audit_exporter=exporter, cost_attributor=getattr(runtime, "cost_attributor", None) or NullCostAttributor()
        )
        for event_type in (ToolInvocationCompleted, ToolInvocationFailed, McpServerStateChanged):
            runtime.event_bus.subscribe(event_type, handler.handle, kind=HandlerKind.EFFECT)


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


def _keys(context: Any) -> tuple[str, str]:
    """A caller's key, for a principal in a tenant holding ``developer``; an ``auditor``'s, for the event stream."""
    auth = context.auth_components
    caller = auth.api_key_store.create_key(principal_id=CALLER, name="caller", tenant_id=TENANT)
    auth.role_store.assign_role(CALLER, "developer")
    observer = auth.api_key_store.create_key(principal_id="svc:canary-observer", name="observer")
    auth.role_store.assign_role("svc:canary-observer", "auditor")
    return caller, observer


def _post(client: Any, key: str, transport: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """One stateless request to ``/mcp`` carrying the transport's baggage and header canaries; its response."""
    params = {**params, "_meta": {**ENVELOPE, "baggage": f"canary.meta={canary('baggage', transport)}"}}
    headers = {
        **HEADERS,
        "Mcp-Method": method,
        "X-API-Key": key,
        "baggage": f"canary.probe={canary('baggage', transport)}",
        "X-Canary-Probe": canary("header", transport),
    }
    if "name" in params:
        headers["Mcp-Name"] = params["name"]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    response = client.post("/mcp", headers=headers, content=body)
    response.raise_for_status()
    text = response.text.lstrip()
    if not text.startswith("{"):  # SSE framing: take the data line
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    return json.loads(text)


def _call(client: Any, key: str, surface: str, transport: str, tool: str) -> dict[str, Any]:
    name, args = prefix(transport) + tool, arguments(tool, transport)
    if surface == "front_door":
        return _post(client, key, transport, "tools/call", {"name": name, "arguments": args})
    calls = [{"mcp_server": SERVERS[transport], "tool": name, "arguments": args}]
    return _post(client, key, transport, "tools/call", {"name": "hangar_call", "arguments": {"calls": calls}})


def _span(span: Any) -> dict[str, Any]:
    return {
        "name": span.name,
        "kind": span.kind.name,
        "status": span.status.status_code.name,
        "status_description": span.status.description,
        "attributes": dict(span.attributes or {}),
        "events": [{"name": e.name, "attributes": dict(e.attributes or {})} for e in span.events],
        "links": [dict(link.attributes or {}) for link in span.links],
    }


def _read_lines(file: Path) -> list[str]:
    return file.read_text(encoding="utf-8").splitlines() if file.exists() else []


def main(surface: str, out: Path, endpoint: str) -> None:
    directory = out.parent
    os.chdir(directory)  # bootstrap keeps its data, the event store among it, under ./data

    from mcp_hangar.logging_config import setup_logging

    log_file = directory / "hangar.log"
    setup_logging(level="INFO", json_format=True, log_file=str(log_file))

    from opentelemetry import trace
    from opentelemetry._logs import get_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    try:
        from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter as InMemoryLogs
    except ImportError:  # the name before 1.39
        from opentelemetry.sdk._logs.export import InMemoryLogExporter as InMemoryLogs

    http = ThreadingHTTPServer(("127.0.0.1", 0), HttpUpstream)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    stdio_record = directory / "stdio_upstream.jsonl"
    http_upstream = f"http://127.0.0.1:{http.server_address[1]}/mcp"

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.context import get_context
    from mcp_hangar.server.lifecycle import warm_the_front_door_catalogue

    # A file, as `serve --http` reads one: `tool_access.mode` is applied while
    # the file is loaded. JSON is YAML.
    config_file = directory / "config.yaml"
    config_file.write_text(json.dumps(_config(surface, endpoint, http_upstream, stdio_record)))
    context = bootstrap(config_path=str(config_file))
    caller_key, observer_key = _keys(context)

    logger_provider = get_logger_provider()
    audit = InMemoryLogs()
    if not isinstance(logger_provider, LoggerProvider):
        raise SystemExit(f"bootstrap registered no SDK logger provider: {type(logger_provider).__name__}")
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(audit))
    spans = InMemorySpanExporter()
    tracer_provider = trace.get_tracer_provider()
    if not isinstance(tracer_provider, TracerProvider):
        raise SystemExit(f"bootstrap registered no SDK tracer provider: {type(tracer_provider).__name__}")
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    _subscribe_other_compliance_formats(context.runtime, directory)

    gate = get_context().approval_gate
    if gate is None:
        raise SystemExit("bootstrap wired no approval gate; nothing would answer the approver canary")
    threading.Thread(target=_deny_every_hold, args=(gate,), daemon=True).start()
    # What `run_http` starts at boot; it returns at once outside the front door.
    warm_the_front_door_catalogue(context.runtime)

    from starlette.testclient import TestClient

    calls: dict[str, Any] = {}
    ws_events: list[dict[str, Any]] = []
    store = context.runtime.event_bus.event_store
    with TestClient(_served_app(context), base_url=BASE_URL) as client:
        with client.websocket_connect("/api/ws/events", headers={"X-API-Key": observer_key}) as ws:
            ws.send_json({"type": "subscribe", "event_types": list(WS_EVENT_TYPES)})
            if ws.receive_json().get("type") != "subscribed":
                raise SystemExit("/api/ws/events did not acknowledge the subscription")
            listed = _post(client, caller_key, "stdio", "tools/list", {})["result"]["tools"]
            for transport in SERVERS:
                for scenario, tool in SCENARIOS.items():
                    calls[f"{transport}:{scenario}"] = _call(client, caller_key, surface, transport, tool)
            # The stream carries what the store holds, for the types asked for;
            # read until it has delivered as many. The parent's timeout ends a
            # run in which it never does.
            expected = sum(1 for _p, _s, e in store.read_all(0, 100_000) if type(e).__name__ in WS_EVENT_TYPES)
            while len(ws_events) < expected:
                ws_events.append(json.loads(ws.receive_text()))

    for server in context.runtime.repository.get_all().values():
        server.shutdown()

    out.write_text(
        json.dumps(
            {
                "surface": surface,
                "listed": [tool["name"] for tool in listed],
                "calls": calls,
                "spans": [_span(s) for s in spans.get_finished_spans()],
                "audit": [
                    {"body": item.log_record.body, "attributes": dict(item.log_record.attributes or {})}
                    for item in audit.get_finished_logs()
                ],
                "compliance": {
                    name: _read_lines(directory / f"{name}.log") for name in ("cef", "leef", "jsonlines", "syslog")
                },
                "logs": _read_lines(log_file),
                "ws_events": ws_events,
                "event_store": [{"stream": s, **e.to_dict()} for _p, s, e in store.read_all(0, 100_000)],
                "upstream_seen": {
                    "stdio": [json.loads(line) for line in _read_lines(stdio_record)],
                    "http": HttpUpstream.seen,
                },
            },
            default=str,
        )
    )
    # Skip interpreter teardown: the OTLP endpoint is unreachable by design, and
    # an atexit flush to it must not stall a run whose results are already written.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), sys.argv[3])
