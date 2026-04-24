"""Tests for outbound W3C TraceContext injection in HTTP provider calls."""

import pathlib

import pytest
from unittest.mock import MagicMock, patch


class TestHttpTraceContextInjection:
    """HTTP provider requests must carry W3C TraceContext headers when in an active trace."""

    def test_inject_trace_context_called_on_outbound_request(self) -> None:
        """inject_trace_context is called when HttpClient.call() makes an outbound HTTP request."""
        from mcp_hangar.http_client import AuthConfig, HttpClient, HttpClientConfig

        client = HttpClient(
            endpoint="http://test-provider:8080",
            auth_config=AuthConfig(),
            http_config=HttpClientConfig(),
        )

        with patch("mcp_hangar.http_client.inject_trace_context") as mock_inject:
            mock_inject.return_value = None
            # Mock the httpx client.post to avoid real HTTP
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.headers = {"Content-Type": "application/json"}
            mock_response.json.return_value = {
                "jsonrpc": "2.0",
                "id": "test",
                "result": {"ok": True},
            }
            with patch.object(client._client, "post", return_value=mock_response):
                client.call("tools/call", {"name": "my_tool", "arguments": {}})

            mock_inject.assert_called_once()
            # The first argument should be a dict (the headers carrier)
            call_args = mock_inject.call_args
            assert isinstance(call_args[0][0], dict), "inject_trace_context must be called with a dict carrier"

    def test_outbound_headers_contain_traceparent_when_trace_active(self) -> None:
        """Headers passed to HTTP request include traceparent when an active trace exists."""
        pytest.importorskip("opentelemetry.sdk.trace")

        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry import trace as otel_trace

        from mcp_hangar.http_client import AuthConfig, HttpClient, HttpClientConfig

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        old_provider = otel_trace.get_tracer_provider()
        otel_trace.set_tracer_provider(provider)

        try:
            client = HttpClient(
                endpoint="http://test-provider:8080",
                auth_config=AuthConfig(),
                http_config=HttpClientConfig(),
            )

            captured_headers: dict[str, str] = {}

            def capture_post(url, *, json=None, timeout=None, headers=None, **kwargs):
                """Capture extra headers sent to httpx.post."""
                if headers:
                    captured_headers.update(headers)
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.headers = {"Content-Type": "application/json"}
                mock_resp.json.return_value = {
                    "jsonrpc": "2.0",
                    "id": "test",
                    "result": {"ok": True},
                }
                return mock_resp

            tracer = otel_trace.get_tracer("test")
            # Patch _initialized so inject_trace_context actually runs
            with patch("mcp_hangar.observability.tracing._initialized", True):
                with tracer.start_as_current_span("test-parent-span"):
                    with patch.object(client._client, "post", side_effect=capture_post):
                        client.call("tools/call", {"name": "t", "arguments": {}})

            assert "traceparent" in captured_headers, (
                f"Expected 'traceparent' in outbound headers, got: {list(captured_headers.keys())}"
            )
        finally:
            otel_trace.set_tracer_provider(old_provider)


class TestStdioNoTraceInjection:
    """StdioClient does NOT inject trace context (stdio has no header mechanism)."""

    def test_stdio_client_has_no_inject_trace_context_call(self) -> None:
        """Confirm stdio_client.py does not import inject_trace_context."""
        src = pathlib.Path("src/mcp_hangar/stdio_client.py").read_text()
        assert "inject_trace_context" not in src, (
            "StdioClient must not inject trace context (stdio has no header mechanism). "
            "This is expected and correct per deployment focus: K8s/Docker first, stdio maintenance-only."
        )
