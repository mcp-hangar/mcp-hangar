"""SEP-414: outbound HTTP requests carry W3C trace context in params._meta.

Complements test_trace_context_injection.py (which covers the HTTP-header path):
per SEP-414 the trace context must also travel in the JSON-RPC params._meta so it
survives across MCP hops regardless of transport.
"""

from unittest.mock import MagicMock, patch

import pytest


def test_http_outbound_puts_traceparent_in_params_meta() -> None:
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from mcp_hangar.http_client import AuthConfig, HttpClient, HttpClientConfig

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    old_provider = otel_trace.get_tracer_provider()
    otel_trace.set_tracer_provider(provider)

    captured: dict = {}

    def capture_post(url, *, json=None, timeout=None, headers=None, **kwargs):
        captured["body"] = json
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "application/json"}
        resp.json.return_value = {"jsonrpc": "2.0", "id": "t", "result": {}}
        return resp

    try:
        client = HttpClient(
            endpoint="http://upstream:8080",
            auth_config=AuthConfig(),
            http_config=HttpClientConfig(),
        )
        tracer = otel_trace.get_tracer("test")
        with patch("mcp_hangar.observability.tracing._initialized", True):
            with tracer.start_as_current_span("parent"):
                with patch.object(client._client, "post", side_effect=capture_post):
                    client.call("tools/call", {"name": "t", "arguments": {}})

        meta = captured["body"]["params"]["_meta"]
        # SEP-414 trace key, un-prefixed, alongside the protocol context.
        assert "traceparent" in meta
        assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    finally:
        otel_trace.set_tracer_provider(old_provider)
