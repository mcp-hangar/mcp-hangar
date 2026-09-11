"""Bootstrap Hangar, call a tool through the served app, and dump its audit records (#1289).

Run as a script, in its own interpreter, by ``test_audit_log_pipeline_bootstrap.py``:
``python _audit_log_harness.py <mode> <out.json> <endpoint>``. Not collected by pytest.

A separate process for the reason ``_trace_harness.py`` gives: the global logger
and tracer providers are registered once per process, and what the bootstrap
does depends on whether anything registered one first. Here nothing has.

What runs is production: ``bootstrap()`` with a config dict, which initialises
observability and then the event handlers, and ``mcp_app_for_serving`` -- the
app ``serve --http`` serves -- under starlette's ``TestClient``, taking stateless
``tools/call hangar_call`` POSTs to ``tests/mock_provider.py`` over stdio. Two
constructors are wrapped to record their arguments and otherwise run unchanged:
the OTLP log exporter and the batch processor. The only additions are in-memory
exporters attached to whichever SDK providers the bootstrap registered, so its
records and spans can be read back.

Modes: ``yaml`` and ``env`` set the OTLP endpoint in the file or the env var
(the parent sets the env); ``none`` sets neither; ``tracing_off`` sets it in the
file with tracing disabled.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"
BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "audit-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
HEADERS = {
    "MCP-Protocol-Version": MODERN_VERSION,
    "Mcp-Method": "tools/call",
    "Mcp-Name": "hangar_call",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

#: mode -> [(call name, traceparent flags or None for no traceparent)]
CALLS: dict[str, list[tuple[str, str | None]]] = {
    "yaml": [("sampled", "01"), ("unsampled", "00")],
    "env": [("sampled", "01")],
    "none": [("sampled", "01")],
    "tracing_off": [("no_trace_context", None)],
}


def caller(call: str) -> tuple[str, str]:
    """The remote caller's (trace id, span id) for a call: distinct per call."""
    n = sorted({name for calls in CALLS.values() for name, _ in calls}).index(call) + 1
    return f"{0x1289_0000 + n:032x}", f"{0xA0D1_0000 + n:016x}"


def _record_constructors(module: Any) -> dict[str, list[Any]]:
    seen: dict[str, list[Any]] = {"endpoints": [], "batched": []}
    exporter_cls = getattr(module, "OTLPLogExporter", None)
    processor_cls = getattr(module, "BatchLogRecordProcessor", None)
    if exporter_cls is None or processor_cls is None:  # a build that constructs no pipeline
        return seen

    def exporter(**kwargs: Any) -> Any:
        built = exporter_cls(**kwargs)
        # What the SDK resolved, not what Hangar passed: with the endpoint in
        # the env, Hangar passes None and the SDK reads it (#1326).
        seen["endpoints"].append(built._endpoint)
        return built

    def processor(inner: Any, *args: Any, **kwargs: Any) -> Any:
        seen["batched"].append(type(inner).__name__)
        return processor_cls(inner, *args, **kwargs)

    module.OTLPLogExporter, module.BatchLogRecordProcessor = exporter, processor
    return seen


def _audit_exporters(runtime: Any) -> list[str]:
    from mcp_hangar.application.event_handlers.audit_event_handler import OTLPAuditEventHandler

    return sorted(
        {
            type(handler.__self__._exporter).__name__
            for entries in runtime.event_bus._handlers.values()
            for handler, _kind in entries
            if isinstance(getattr(handler, "__self__", None), OTLPAuditEventHandler)
        }
    )


def _hangar_call(client: Any, call: str, flags: str | None) -> dict[str, Any]:
    meta: dict[str, Any] = dict(ENVELOPE)
    trace_id, span_id = caller(call)
    if flags is not None:
        meta["traceparent"] = f"00-{trace_id}-{span_id}-{flags}"
    params = {
        "name": "hangar_call",
        "arguments": {"calls": [{"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}]},
        "_meta": meta,
    }
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    response = client.post("/mcp", headers=HEADERS, content=body)
    response.raise_for_status()
    text = response.text.lstrip()
    if not text.startswith("{"):  # SSE framing: take the data line
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    result = json.loads(text)["result"]
    return {"trace_id": trace_id, "remote_span_id": span_id, "batch": json.loads(result["content"][0]["text"])}


def main(mode: str, out: Path, endpoint: str) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data
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

    from mcp_hangar.infrastructure.observability import otlp_audit_exporter

    seen = _record_constructors(otlp_audit_exporter)
    config: dict[str, Any] = {
        "mcp_servers": {"math": {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]}}
    }
    if mode in ("yaml", "tracing_off"):
        config["observability"] = {"tracing": {"otlp_endpoint": endpoint, "enabled": mode == "yaml"}}

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    context = bootstrap(config_dict=config)

    provider = get_logger_provider()
    logs = InMemoryLogs()
    if isinstance(provider, LoggerProvider):
        provider.add_log_record_processor(SimpleLogRecordProcessor(logs))
    spans = InMemorySpanExporter()
    tracer_provider = trace.get_tracer_provider()
    if isinstance(tracer_provider, TracerProvider):
        tracer_provider.add_span_processor(SimpleSpanProcessor(spans))

    from starlette.testclient import TestClient

    with TestClient(mcp_app_for_serving(context.mcp_server), base_url=BASE_URL) as client:
        calls = {name: _hangar_call(client, name, flags) for name, flags in CALLS[mode]}

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    records = []
    for item in logs.get_finished_logs():
        r = item.log_record
        records.append(
            {
                "body": r.body,
                "attributes": dict(r.attributes or {}),
                "trace_id": format(r.trace_id or 0, "032x"),
                "span_id": format(r.span_id or 0, "016x"),
                "sampled": bool(r.trace_flags and r.trace_flags.sampled),
            }
        )
    out.write_text(
        json.dumps(
            {
                "audit_exporters": _audit_exporters(context.runtime),
                "logger_provider": type(provider).__name__,
                "owned": provider is getattr(otlp_audit_exporter, "_audit_provider", None),
                **seen,
                "calls": calls,
                "records": records,
                "spans": [
                    {
                        "name": s.name,
                        "trace_id": f"{s.context.trace_id:032x}",
                        "span_id": f"{s.context.span_id:016x}",
                    }
                    for s in spans.get_finished_spans()
                ],
            }
        )
    )
    # Skip interpreter teardown: the endpoint is unreachable by design, and an
    # atexit flush to it must not stall a run whose results are already written.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), sys.argv[3])
