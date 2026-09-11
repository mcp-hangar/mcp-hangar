"""SEP-414: outbound HTTP requests carry W3C trace context in params._meta.

Complements test_trace_context_injection.py (which covers the HTTP-header path):
per SEP-414 the trace context must also travel in the JSON-RPC params._meta so it
survives across MCP hops regardless of transport.

Both carriers name the CLIENT span of the send that carries them, as over stdio.
``_meta`` used to be built before that span opened, so it named the caller's span,
or carried no traceparent without one (#1271). Hence span IDs, not key presence.
"""

from contextlib import nullcontext
import copy
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.protocol import SUPPORTED_PROTOCOL_VERSION

pytestmark = pytest.mark.otel_sdk

VERSION_KEY = "io.modelcontextprotocol/protocolVersion"

# HttpClient entry point -> (method, params, name of its CLIENT span).
SENDS: dict[str, tuple[str, dict[str, Any], str]] = {
    "call": ("tools/call", {"name": "t", "arguments": {}}, "execute_tool t"),
    "notify": ("notifications/progress", {"progressToken": "p", "progress": 1}, "notifications/progress"),
}


@pytest.fixture()
def otel():
    """Hangar's spans go to a local SDK provider and in-memory exporter, never the global one."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with (
        patch("mcp_hangar.observability.tracing.get_tracer", side_effect=provider.get_tracer),
        # inject_trace_context is gated on Hangar's tracing being active.
        patch("mcp_hangar.observability.tracing._initialized", True),
    ):
        yield provider, exporter


def _send(op: str, params: dict[str, Any], times: int = 1) -> list[tuple[dict[str, Any], dict[str, str]]]:
    """Send through ``HttpClient.<op>`` with httpx's post patched; return each (body, headers) posted."""
    from mcp_hangar.http_client import AuthConfig, HttpClient, HttpClientConfig

    client = HttpClient(endpoint="http://upstream:8080", auth_config=AuthConfig(), http_config=HttpClientConfig())
    # The protocol envelope rides once the connection is known to accept
    # it (#1211) -- the handshake decides that, not a default.
    client.modern_envelope = True
    posted: list[tuple[dict[str, Any], dict[str, str]]] = []

    def capture_post(url, *, json=None, timeout=None, headers=None, **kwargs):
        posted.append((json, dict(headers or {})))
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "application/json"}
        resp.json.return_value = {"jsonrpc": "2.0", "id": "t", "result": {}}
        return resp

    with patch.object(client._client, "post", side_effect=capture_post):
        for _ in range(times):
            getattr(client, op)(SENDS[op][0], params)
    return posted


def _ids(traceparent: str | None) -> tuple[str, str] | None:
    """(trace id, span id) named by a W3C traceparent, or None."""
    if not traceparent:
        return None
    _version, trace_id, span_id, _flags = traceparent.split("-")
    return trace_id, span_id


def _client_spans(exporter) -> list[Any]:
    from opentelemetry.trace import SpanKind

    return [s for s in exporter.get_finished_spans() if s.kind is SpanKind.CLIENT]


def _named(span) -> tuple[str, str]:
    ctx = span.get_span_context()
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"


@pytest.mark.parametrize("ambient", [True, False], ids=["under-a-parent", "no-parent"])
@pytest.mark.parametrize("op", SENDS)
def test_meta_and_header_both_name_the_client_span(otel, op: str, ambient: bool) -> None:
    provider, exporter = otel
    _method, params, span_name = SENDS[op]

    with provider.get_tracer("test").start_as_current_span("parent") if ambient else nullcontext() as parent:
        [(body, headers)] = _send(op, params)

    [client_span] = _client_spans(exporter)
    assert client_span.name == span_name
    meta = body["params"]["_meta"]
    assert _ids(meta.get("traceparent")) == _named(client_span)
    assert _ids(headers.get("traceparent")) == _named(client_span)
    assert meta[VERSION_KEY] == SUPPORTED_PROTOCOL_VERSION
    # The CLIENT span itself still parents on the caller's span, or is a root.
    parent_id = parent.get_span_context().span_id if parent else None
    assert (client_span.parent.span_id if client_span.parent else None) == parent_id


@pytest.mark.parametrize("op", SENDS)
def test_every_send_builds_fresh_carriers_from_the_callers_params(otel, op: str) -> None:
    _provider, exporter = otel
    _method, params, _span_name = SENDS[op]
    stale = f"00-{'1' * 32}-{'2' * 16}-01"
    caller = {**params, "_meta": {VERSION_KEY: "2025-11-25", "traceparent": stale}}
    before = copy.deepcopy(caller)

    posted = _send(op, caller, times=2)

    assert caller == before, "the caller's params must not be mutated"
    sent = [body["params"] for body, _headers in posted]
    # Each re-send carries its own CLIENT span, not the caller's stale context
    # or the one injected for an earlier send.
    assert [_ids(p["_meta"]["traceparent"]) for p in sent] == [_named(s) for s in _client_spans(exporter)]
    # Caller-set protocol keys win; the rest of the params ride unchanged.
    assert [p["_meta"][VERSION_KEY] for p in sent] == ["2025-11-25"] * 2
    assert [{k: v for k, v in p.items() if k != "_meta"} for p in sent] == [params] * 2
