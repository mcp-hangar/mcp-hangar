"""The standing GET stream and the DELETE teardown (#882).

Streamable HTTP gives an upstream two ways to reach its client; we used
neither for anything but request/response, so every server-initiated message
was silently unreachable and a negotiated session was abandoned rather than
closed. These cover the transport half (HttpClient) and the routing half
(McpServer._route_upstream_message).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from mcp_hangar.http_client import AuthConfig, HttpClient, HttpClientConfig


def _http_client() -> HttpClient:
    return HttpClient(
        endpoint="http://upstream:8080",
        auth_config=AuthConfig(),
        http_config=HttpClientConfig(),
    )


def _stream_response(status_code: int, body: str = ""):
    response = MagicMock()
    response.status_code = status_code
    response.iter_text.return_value = iter([body])

    @contextmanager
    def ctx(*_args, **_kwargs):
        yield response

    return ctx


def _wait_for(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestGetStream:
    def test_events_are_decoded_and_dispatched(self) -> None:
        client = _http_client()
        body = (
            'data: {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}\n\n'
            'data: {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}\n\n'
        )
        seen: list[dict] = []

        with patch.object(client._client, "stream", new=_stream_response(200, body)):
            client.start_notification_stream(seen.append)
            assert _wait_for(lambda: len(seen) == 2)
        client._sse_running = False

        assert seen[0]["method"] == "notifications/progress"
        assert seen[1]["method"] == "notifications/tools/list_changed"

    def test_a_405_means_no_channel_and_no_retry_loop(self) -> None:
        """404/405 is an upstream without the channel -- a normal answer, not an error to hammer."""
        client = _http_client()
        calls = {"n": 0}
        inner = _stream_response(405)

        @contextmanager
        def counting(*args, **kwargs):
            calls["n"] += 1
            with inner(*args, **kwargs) as r:
                yield r

        with patch.object(client._client, "stream", new=counting):
            client.start_notification_stream(lambda _msg: None)
            assert _wait_for(lambda: not client._sse_thread.is_alive())

        assert calls["n"] == 1

    def test_a_bad_handler_does_not_kill_the_channel(self) -> None:
        client = _http_client()
        body = (
            'data: {"jsonrpc": "2.0", "method": "notifications/progress"}\n\n'
            'data: {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}\n\n'
        )
        seen: list[str] = []

        def handler(msg: dict) -> None:
            seen.append(msg["method"])
            raise RuntimeError("handler bug")

        with patch.object(client._client, "stream", new=_stream_response(200, body)):
            client.start_notification_stream(handler)
            assert _wait_for(lambda: len(seen) == 2)
        client._sse_running = False

    def test_the_session_header_rides_along(self) -> None:
        client = _http_client()
        client._mcp_session_id = "sess-9"
        captured = {}

        @contextmanager
        def capture(_method, _url, headers=None, timeout=None):
            captured.update(headers or {})
            response = MagicMock()
            response.status_code = 405
            yield response

        with patch.object(client._client, "stream", new=capture):
            client.start_notification_stream(lambda _msg: None)
            assert _wait_for(lambda: "Mcp-Session-Id" in captured)

        assert captured["Mcp-Session-Id"] == "sess-9"


class TestDeleteTeardown:
    def test_close_deletes_a_negotiated_session(self) -> None:
        client = _http_client()
        client._mcp_session_id = "sess-1"

        with patch.object(client._client, "delete") as delete:
            client.close()

        assert delete.call_args.kwargs["headers"]["Mcp-Session-Id"] == "sess-1"

    def test_close_without_a_session_sends_nothing(self) -> None:
        """A modern stateless upstream has no session to delete."""
        client = _http_client()

        with patch.object(client._client, "delete") as delete:
            client.close()

        delete.assert_not_called()

    def test_a_failed_delete_does_not_fail_the_shutdown(self) -> None:
        client = _http_client()
        client._mcp_session_id = "sess-1"

        with patch.object(client._client, "delete", side_effect=OSError("gone")):
            client.close()  # must not raise

        assert client.is_alive() is False


class TestRouting:
    def _mcp_server(self):
        from mcp_hangar.domain.model.mcp_server import McpServer

        server = MagicMock(spec=McpServer)
        server.mcp_server_id = "m"
        server._route_upstream_message = McpServer._route_upstream_message.__get__(server)
        return server

    def test_list_changed_triggers_rediscovery(self) -> None:
        server = self._mcp_server()
        server._route_upstream_message({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        server._refresh_tools.assert_called_once()

    def test_a_server_initiated_request_is_not_answered_by_accident(self) -> None:
        """We declare no client capabilities, so a request (has an id) is unsupported."""
        server = self._mcp_server()
        server._route_upstream_message({"jsonrpc": "2.0", "id": 7, "method": "sampling/createMessage"})
        server._refresh_tools.assert_not_called()

    def test_an_unrouted_notification_is_ignored(self) -> None:
        server = self._mcp_server()
        server._route_upstream_message({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
        server._refresh_tools.assert_not_called()
