"""Tests for outbound W3C TraceContext injection in HTTP provider calls."""

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

            # Called twice: once for params._meta (SEP-414) and once for HTTP headers.
            assert mock_inject.call_count == 2
            for call_args in mock_inject.call_args_list:
                assert isinstance(call_args.args[0], dict), "inject_trace_context must be called with a dict carrier"

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


class TestStdioTraceContextInjection:
    """StdioClient propagates W3C TraceContext into the MCP request `_meta` field.

    stdio has no headers, but MCP's `_meta` is the metadata channel — mirroring
    the HTTP transport keeps distributed tracing intact across stdio upstreams.
    """

    @staticmethod
    def _mock_client():
        import subprocess
        from mcp_hangar.stdio_client import StdioClient

        mock_popen = MagicMock(spec=subprocess.Popen)
        mock_popen.pid = 4242
        mock_popen.stdin = MagicMock()
        mock_popen.stdout = MagicMock()
        mock_popen.stderr = MagicMock()
        mock_popen.poll.return_value = None
        mock_popen.stdout.readline.return_value = ""
        with patch("mcp_hangar.stdio_client.threading.Thread"):
            return StdioClient(mock_popen), mock_popen

    def test_stdio_request_meta_contains_traceparent_when_trace_active(self) -> None:
        """The request written to stdin carries `_meta.traceparent` under an active trace."""
        import json
        from queue import Queue

        pytest.importorskip("opentelemetry.sdk.trace")
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry import trace as otel_trace

        client, mock_popen = self._mock_client()

        written: list[str] = []
        mock_popen.stdin.write.side_effect = lambda data: written.append(data)

        provider = TracerProvider()
        old_provider = otel_trace.get_tracer_provider()
        otel_trace.set_tracer_provider(provider)
        try:
            tracer = otel_trace.get_tracer("test")
            with patch("mcp_hangar.observability.tracing._initialized", True):
                with tracer.start_as_current_span("parent"):
                    with patch.object(Queue, "get", return_value={"jsonrpc": "2.0", "result": {}}):
                        client.call("tools/call", {"name": "t", "arguments": {}})
        finally:
            otel_trace.set_tracer_provider(old_provider)

        assert written, "expected a request to be written to stdin"
        sent = json.loads(written[0])
        assert "traceparent" in sent["params"].get("_meta", {}), (
            f"expected traceparent in _meta, got params={sent['params']}"
        )

    def test_stdio_injection_does_not_mutate_caller_params(self) -> None:
        """Injecting `_meta` must not mutate the caller's params dict."""
        from queue import Queue

        pytest.importorskip("opentelemetry.sdk.trace")
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry import trace as otel_trace

        client, _ = self._mock_client()
        caller_params: dict = {"name": "t", "arguments": {}}

        provider = TracerProvider()
        old_provider = otel_trace.get_tracer_provider()
        otel_trace.set_tracer_provider(provider)
        try:
            tracer = otel_trace.get_tracer("test")
            with patch("mcp_hangar.observability.tracing._initialized", True):
                with tracer.start_as_current_span("parent"):
                    with patch.object(Queue, "get", return_value={"jsonrpc": "2.0", "result": {}}):
                        client.call("tools/call", caller_params)
        finally:
            otel_trace.set_tracer_provider(old_provider)

        assert "_meta" not in caller_params, "caller's params dict must not be mutated"
