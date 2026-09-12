"""Drive `hangar_call` through the served app and dump the traces it produced (#1284).

Run as a script, in its own interpreter, by ``test_trace_propagation_e2e.py``:
``python _trace_harness.py <out.json>``. Not collected by pytest.

Why a separate process. OpenTelemetry accepts one global tracer provider per
process, and Hangar's tracing bootstrap is a set of one-shot transitions on that
global: whether a provider was registered before Hangar's, whether Hangar's own
init ran, whether it was shut down and initialised again. #1283 changes what
each of those transitions does. A fixture sharing the pytest process would see
whichever transitions earlier tests happened to make, so its result would depend
on test order today and on #1283's semantics tomorrow.

In a fresh interpreter none of those transitions has happened. The harness makes
the one call production makes -- Hangar's own ``init_tracing()`` -- exactly once,
with no provider registered before it, and attaches an in-memory processor to the
provider that call registered. That is the owned-provider path, which behaves the
same before and after #1283: it never meets "an external provider is already
registered" and never re-initialises after a shutdown. ``get_tracer``,
``get_context`` and the propagation helpers run unpatched.

What is driven: ``wrap_front_door_routing(build_serving_mcp_server().streamable_http_app())``
under starlette's ``TestClient`` -- the composition ``serve --http`` serves -- with
stateless ``tools/call hangar_call`` POSTs carrying ``_meta.traceparent``. Two
upstreams: ``tests/mock_provider.py`` over stdio (it records the ``_meta`` it
receives) and an in-process HTTP upstream here (it records headers and ``_meta``).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any, ClassVar

MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"

# The SDK's DNS-rebinding protection wants a loopback Host with a port.
BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "trace-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
HEADERS = {
    "MCP-Protocol-Version": MODERN_VERSION,
    "Mcp-Method": "tools/call",
    "Mcp-Name": "hangar_call",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

#: What ``web``'s ``leak`` tool says in its ``isError: true`` result. It stands in
#: for a token or personal data a tool echoes, and must reach no span.
TOOL_ERROR_TEXT = "canary-isError-text-3d71"

#: scenario -> (upstream, tool, arguments). ``math`` is stdio, ``web`` is HTTP.
#: ``math``'s policy denies ``multiply``; ``divide`` by zero is an upstream error;
#: ``leak`` answers ``isError: true`` with ``TOOL_ERROR_TEXT``.
SCENARIOS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "stdio_success": ("math", "add", {"a": 1, "b": 2}),
    "http_success": ("web", "add", {"a": 1, "b": 2}),
    "denied": ("math", "multiply", {"a": 2, "b": 3}),
    "upstream_failure": ("math", "divide", {"a": 1, "b": 0}),
    "tool_error": ("web", "leak", {}),
    "concurrent_1": ("web", "add", {"a": 1, "b": 1}),
    "concurrent_2": ("web", "add", {"a": 2, "b": 2}),
}


def remote_parent(scenario: str) -> tuple[str, str]:
    """The caller's (trace id, span id) for a scenario: distinct per scenario."""
    n = list(SCENARIOS).index(scenario) + 1
    return f"{0x1284_0000 + n:032x}", f"{0x5EED_0000 + n:016x}"


class _HttpUpstream(BaseHTTPRequestHandler):
    """A minimal JSON-answering MCP upstream that records what it was sent."""

    seen: ClassVar[list[dict[str, Any]]] = []
    #: Set for the concurrent scenario: each ``tools/call`` waits for the other,
    #: so both requests are provably in flight at once.
    rendezvous: ClassVar[threading.Barrier | None] = None

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        method = request.get("method")
        meta = (request.get("params") or {}).get("_meta") or {}
        seen = {"method": method, "header": self.headers.get("traceparent"), "meta": meta.get("traceparent")}
        self.seen.append(seen)
        if "id" not in request:  # a notification
            self._send(202, b"")
            return
        if method == "tools/call" and self.rendezvous is not None:
            try:
                self.rendezvous.wait()
                seen["overlapped"] = True
            except threading.BrokenBarrierError:
                seen["overlapped"] = False
        tools = [{"name": name, "inputSchema": {"type": "object"}} for name in ("add", "leak")]
        if (request.get("params") or {}).get("name") == "leak":
            call_result = {"isError": True, "content": [{"type": "text", "text": TOOL_ERROR_TEXT}]}
        else:
            call_result = {"content": [{"type": "text", "text": "ok"}]}
        answer = {
            "initialize": {"result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}},
            "tools/list": {"result": {"tools": tools}},
            "tools/call": {"result": call_result},
        }.get(method, {"error": {"code": -32601, "message": f"Unknown method: {method}"}})
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _observe_hangars_own_provider() -> Any:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from mcp_hangar.observability.tracing import init_tracing

    # console_export only guarantees init has an exporter to add whatever the
    # OTLP settings are; its output lands in the captured stdio.
    if not init_tracing(console_export=True):
        raise SystemExit("init_tracing() did not initialise tracing in a fresh process")
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        raise SystemExit(f"init_tracing() registered no SDK provider: {type(provider).__name__}")
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def _register_upstreams(http_endpoint: str, stdio_record: Path) -> Any:
    from mcp_hangar.application.commands import InvokeToolCommand, StartMcpServerCommand
    from mcp_hangar.application.commands.handlers import InvokeToolHandler, StartMcpServerHandler
    from mcp_hangar.bootstrap.runtime import create_runtime
    from mcp_hangar.domain.model import McpServer
    from mcp_hangar.domain.services import get_tool_access_resolver
    from mcp_hangar.domain.value_objects import ToolAccessPolicy
    from mcp_hangar.infrastructure.command_bus import CommandBus
    from mcp_hangar.server.context import init_context

    bus = CommandBus()
    runtime = create_runtime(command_bus=bus)
    # The two commands the invoke path sends (cold start, then the call), wired
    # the way bootstrap() wires them.
    bus.register(StartMcpServerCommand, StartMcpServerHandler(runtime.repository, runtime.event_bus))
    bus.register(InvokeToolCommand, InvokeToolHandler(runtime.repository, runtime.event_bus))
    stdio = McpServer(
        mcp_server_id="math",
        mode="subprocess",
        command=[sys.executable, str(MOCK_PROVIDER)],
        env={"MOCK_PROVIDER_RECORD": str(stdio_record)},
    )
    runtime.repository.add("math", stdio)
    runtime.repository.add("web", McpServer(mcp_server_id="web", mode="remote", endpoint=http_endpoint))
    get_tool_access_resolver().set_mcp_server_policy("math", ToolAccessPolicy(deny_list=("multiply",)))
    init_context(runtime)
    return runtime


def _hangar_call(client: Any, scenario: str) -> dict[str, Any]:
    server, tool, arguments = SCENARIOS[scenario]
    trace_id, span_id = remote_parent(scenario)
    params = {
        "name": "hangar_call",
        "arguments": {"calls": [{"mcp_server": server, "tool": tool, "arguments": arguments}]},
        "_meta": {**ENVELOPE, "traceparent": f"00-{trace_id}-{span_id}-01"},
    }
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    response = client.post("/mcp", headers=HEADERS, content=body)
    response.raise_for_status()
    text = response.text.lstrip()
    if not text.startswith("{"):  # SSE framing: take the data line
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    result = json.loads(text)["result"]
    return {
        "trace_id": trace_id,
        "remote_span_id": span_id,
        "is_error": result.get("isError", False),
        "batch": json.loads(result["content"][0]["text"]),
    }


def _span(span: Any) -> dict[str, Any]:
    parent = span.parent
    return {
        "name": span.name,
        "kind": span.kind.name,
        "trace_id": f"{span.context.trace_id:032x}",
        "span_id": f"{span.context.span_id:016x}",
        "parent_id": f"{parent.span_id:016x}" if parent else None,
        "parent_is_remote": bool(parent and parent.is_remote),
        "status": span.status.status_code.name,
        # Everything else a span exports that could carry text.
        "status_description": span.status.description,
        "attributes": dict(span.attributes),
        "events": [{"name": e.name, "attributes": dict(e.attributes)} for e in span.events],
    }


def main(out: Path) -> None:
    exporter = _observe_hangars_own_provider()

    from starlette.testclient import TestClient

    from mcp_hangar.fastmcp_server.modern_surface import wrap_front_door_routing
    from mcp_hangar.server.bootstrap import build_serving_mcp_server

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _HttpUpstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    stdio_record = out.with_suffix(".stdio.jsonl")
    runtime = _register_upstreams(f"http://127.0.0.1:{upstream.server_address[1]}/mcp", stdio_record)

    scenarios: dict[str, Any] = {}
    app = wrap_front_door_routing(build_serving_mcp_server().streamable_http_app())
    with TestClient(app, base_url=BASE_URL) as client:
        for scenario in ("stdio_success", "http_success", "denied", "upstream_failure", "tool_error"):
            scenarios[scenario] = _hangar_call(client, scenario)
        _HttpUpstream.rendezvous = threading.Barrier(2, timeout=10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = {name: pool.submit(_hangar_call, client, name) for name in ("concurrent_1", "concurrent_2")}
            scenarios.update({name: future.result() for name, future in pending.items()})

    for server in runtime.repository.get_all().values():
        server.shutdown()
    stdio_seen = [json.loads(line) for line in stdio_record.read_text().splitlines()] if stdio_record.exists() else []
    out.write_text(
        json.dumps(
            {
                "scenarios": scenarios,
                "spans": [_span(span) for span in exporter.get_finished_spans()],
                "stdio_seen": stdio_seen,
                "http_seen": _HttpUpstream.seen,
            }
        )
    )
    # Skip interpreter teardown: the harness does not own the exporter
    # configuration, and an atexit flush to wherever it points must not stall
    # or fail a run whose results are already written.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
