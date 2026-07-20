"""Transport message metrics: messages_sent/received + message_size_bytes.

Also asserts the never-emitted HTTP pool/SSE gauges were removed.
"""

from __future__ import annotations

import subprocess
from queue import Queue
from unittest.mock import MagicMock, patch

from mcp_hangar import metrics as m


class TestClassifyJsonRpcMessage:
    def test_response(self) -> None:
        assert m.classify_jsonrpc_message({"id": 1, "result": {}}) == "response"

    def test_error(self) -> None:
        assert m.classify_jsonrpc_message({"id": 1, "error": {"code": -1}}) == "error"

    def test_notification(self) -> None:
        # A request with no id is a server-initiated notification.
        assert m.classify_jsonrpc_message({"method": "notifications/progress"}) == "notification"

    def test_error_wins_over_method(self) -> None:
        assert m.classify_jsonrpc_message({"method": "x", "error": {}}) == "error"


class TestRecordHelpers:
    def test_record_message_sent_emits(self) -> None:
        m.record_message_sent("srv-sent-iso", "tools/call", 128)
        out = m.get_metrics()
        assert 'mcp_hangar_messages_sent_total{mcp_server="srv-sent-iso",method="tools/call"}' in out
        assert 'mcp_hangar_message_size_bytes_count{direction="sent",mcp_server="srv-sent-iso"}' in out

    def test_record_message_received_emits(self) -> None:
        m.record_message_received("srv-recv-iso", "response", 64)
        out = m.get_metrics()
        assert 'mcp_hangar_messages_received_total{mcp_server="srv-recv-iso",type="response"}' in out
        assert 'mcp_hangar_message_size_bytes_count{direction="received",mcp_server="srv-recv-iso"}' in out


class TestRemovedMetricsAbsent:
    def test_pool_and_sse_gauges_gone(self) -> None:
        out = m.get_metrics()
        assert "mcp_hangar_http_connection_pool_size" not in out
        assert "mcp_hangar_http_sse_streams_active" not in out
        assert "mcp_hangar_http_sse_events" not in out

    def test_symbols_removed_from_module(self) -> None:
        assert not hasattr(m, "HTTP_CONNECTION_POOL_SIZE")
        assert not hasattr(m, "HTTP_SSE_STREAMS_ACTIVE")
        assert not hasattr(m, "HTTP_SSE_EVENTS_TOTAL")


class TestStdioClientMessageMetrics:
    @staticmethod
    def _mock_popen(readline_returns=("",)):
        p = MagicMock(spec=subprocess.Popen)
        p.pid = 7777
        p.stdin = MagicMock()
        p.stdout = MagicMock()
        p.stderr = MagicMock()
        p.poll.return_value = None
        p.stdout.readline.side_effect = list(readline_returns)
        return p

    def test_call_records_sent(self) -> None:
        from mcp_hangar.stdio_client import StdioClient

        popen = self._mock_popen()
        with patch("mcp_hangar.stdio_client.threading.Thread"):
            client = StdioClient(popen, mcp_server_id="stdio-sent-iso")

        with patch.object(Queue, "get", return_value={"jsonrpc": "2.0", "id": "x", "result": {}}):
            client.call("tools/call", {"name": "t"})

        out = m.get_metrics()
        assert 'mcp_hangar_messages_sent_total{mcp_server="stdio-sent-iso",method="tools/call"}' in out

    def test_reader_loop_records_received(self) -> None:
        from mcp_hangar.stdio_client import StdioClient

        # One JSON line, then EOF ("") so the loop records-received then exits.
        line = '{"jsonrpc":"2.0","id":"abc","result":{"ok":true}}\n'
        popen = self._mock_popen(readline_returns=(line, ""))
        with patch("mcp_hangar.stdio_client.threading.Thread"):
            client = StdioClient(popen, mcp_server_id="stdio-recv-iso")

        client._reader_loop()

        out = m.get_metrics()
        assert 'mcp_hangar_messages_received_total{mcp_server="stdio-recv-iso",type="response"}' in out


class TestHttpClientMessageMetrics:
    def test_call_records_sent_and_received(self) -> None:
        from mcp_hangar.http_client import AuthConfig, HttpClient, HttpClientConfig

        client = HttpClient(
            endpoint="http://iso-msg-provider:8080",
            auth_config=AuthConfig(),
            http_config=HttpClientConfig(),
            mcp_server_id="http-msg-iso",
        )

        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "application/json"}
        resp.content = b'{"jsonrpc":"2.0","id":"x","result":{}}'
        resp.json.return_value = {"jsonrpc": "2.0", "id": "x", "result": {}}

        with patch.object(client._client, "post", return_value=resp):
            client.call("tools/call", {"name": "t", "arguments": {}})

        out = m.get_metrics()
        assert 'mcp_hangar_messages_sent_total{mcp_server="http-msg-iso",method="tools/call"}' in out
        assert 'mcp_hangar_messages_received_total{mcp_server="http-msg-iso",type="response"}' in out
