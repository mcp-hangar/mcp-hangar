"""Drive a REST route and an MCP management tool, and dump the dispatch spans (#1297).

Run as a script, in its own interpreter, by ``test_dispatch_spans_bootstrap.py``:
``python _dispatch_span_harness.py <out.json>``. Not collected by pytest.

A separate process for the reason ``_trace_harness.py`` and ``_audit_log_harness.py``
give: OpenTelemetry accepts one global tracer provider per process, and what
Hangar's bootstrap does depends on whether anything registered one first. In a
fresh interpreter nothing has.

What runs is production. ``bootstrap()`` with a config dict builds the buses the
way `serve` does -- which is the point here, because the middleware on the
command bus is registered by that bootstrap and by nothing a test constructs.
The unit tests build `RateLimitMiddleware` by hand and prove the span shape;
this proves the bus a real gateway uses is the bus that was instrumented.

Two entry points, neither of which goes through the batch executor:

* the REST router's ``GET /mcp_servers``, which dispatches through
  ``dispatch_query`` in the protected API middleware;
* ``tools/call hangar_list`` over ``/mcp``, a management tool that calls the
  query bus directly.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"
BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
HEADERS = {
    "MCP-Protocol-Version": MODERN_VERSION,
    "Mcp-Method": "tools/call",
    "Mcp-Name": "hangar_list",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "dispatch-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _span(span: Any) -> dict[str, Any]:
    return {
        "name": span.name,
        "attributes": {key: value for key, value in (span.attributes or {}).items()},
        "parent": f"{span.parent.span_id:016x}" if span.parent else None,
        "span_id": f"{span.context.span_id:016x}",
    }


def _management_tool(client: Any) -> int:
    """`tools/call hangar_list` over the served `/mcp` surface."""
    params = {"name": "hangar_list", "arguments": {}, "_meta": dict(ENVELOPE)}
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    return client.post("/mcp", headers=HEADERS, content=body).status_code


def main(out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    config: dict[str, Any] = {
        # console_export only guarantees init has an exporter to add; without
        # one `init_tracing` registers no provider and there is nothing to read
        # spans back from. Its output lands in the captured stdio.
        "observability": {"tracing": {"enabled": True, "console_export": True}},
        "mcp_servers": {"math": {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]}},
    }
    context = bootstrap(config_dict=config)

    spans = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        raise SystemExit(f"bootstrap registered no SDK provider: {type(provider).__name__}")
    provider.add_span_processor(SimpleSpanProcessor(spans))

    from starlette.testclient import TestClient

    from mcp_hangar.server.api import create_api_router

    # The REST surface, built the way the served process builds it.
    api = create_api_router(auth_components=getattr(context, "auth_components", None))
    with TestClient(api, base_url=BASE_URL) as client:
        rest_status = client.get("/mcp_servers").status_code

    with TestClient(mcp_app_for_serving(context.mcp_server), base_url=BASE_URL) as client:
        tool_status = _management_tool(client)

    for server in context.runtime.repository.get_all().values():
        server.shutdown()

    # What the bootstrap put on the command bus. The rate limit is the reason
    # this issue exists, and whether it is registered here is the thing a
    # hand-built bus cannot tell us.
    bus = getattr(context.runtime, "command_bus", None)
    middleware = [type(mw).__name__ for mw in getattr(bus, "_middleware", [])]

    out.write_text(
        json.dumps(
            {
                "rest_status": rest_status,
                "tool_status": tool_status,
                "command_bus_middleware": middleware,
                "spans": [_span(span) for span in spans.get_finished_spans()],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
