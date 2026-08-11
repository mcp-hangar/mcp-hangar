"""The notification primitive both transports were missing (#881).

`call` mints a `uuid4` id unconditionally and blocks on the matching response,
so before this there was no way to express a notification on either transport
-- which is why the MCP lifecycle was never finished. These cover the primitive
itself; `test_initialized_notification` covers the handshake that uses it.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.domain.exceptions import ClientError
from mcp_hangar.http_client import AuthConfig, HttpClient, HttpClientConfig
from mcp_hangar.stdio_client import StdioClient

_PV = "io.modelcontextprotocol/protocolVersion"


def _http_client() -> HttpClient:
    return HttpClient(
        endpoint="http://upstream:8080",
        auth_config=AuthConfig(),
        http_config=HttpClientConfig(),
    )


def _accepted() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 202
    resp.headers = {}
    return resp


class TestHttp:
    def test_no_id_is_sent(self) -> None:
        """The defining property: an id would make it a request the upstream must answer."""
        client = _http_client()

        with patch.object(client._client, "post", return_value=_accepted()) as post:
            client.notify("notifications/initialized")

        body = post.call_args.kwargs["json"]
        assert "id" not in body
        assert body["method"] == "notifications/initialized"
        assert body["jsonrpc"] == "2.0"

    def test_carries_the_protocol_envelope(self) -> None:
        client = _http_client()

        with patch.object(client._client, "post", return_value=_accepted()) as post:
            client.notify("notifications/initialized")

        assert post.call_args.kwargs["json"]["params"]["_meta"][_PV] == "2026-07-28"

    def test_a_legacy_connection_is_not_sent_the_modern_envelope(self) -> None:
        """Era gate: from mcp 2.0.0 a legacy connection rejects it with -32600."""
        client = _http_client()
        client.modern_envelope = False

        with patch.object(client._client, "post", return_value=_accepted()) as post:
            client.notify("notifications/initialized")

        assert _PV not in post.call_args.kwargs["json"]["params"]["_meta"]

    def test_the_session_header_rides_along(self) -> None:
        client = _http_client()
        client._mcp_session_id = "sess-1"

        with patch.object(client._client, "post", return_value=_accepted()) as post:
            client.notify("notifications/initialized")

        assert post.call_args.kwargs["headers"]["Mcp-Session-Id"] == "sess-1"

    def test_a_rejected_notification_raises(self) -> None:
        """No result to return, but a notification that did not arrive is worth knowing."""
        client = _http_client()
        rejected = MagicMock()
        rejected.status_code = 400
        rejected.headers = {}

        with patch.object(client._client, "post", return_value=rejected):
            with pytest.raises(ClientError, match="notify_rejected"):
                client.notify("notifications/initialized")

    def test_a_transport_failure_raises(self) -> None:
        client = _http_client()

        with patch.object(client._client, "post", side_effect=OSError("connection refused")):
            with pytest.raises(ClientError, match="notify_failed"):
                client.notify("notifications/initialized")

    def test_a_closed_client_refuses(self) -> None:
        client = _http_client()
        client.close()

        with pytest.raises(ClientError, match="client_closed"):
            client.notify("notifications/initialized")


class TestStdio:
    @staticmethod
    def _client() -> tuple[StdioClient, MagicMock]:
        popen = MagicMock()
        popen.poll.return_value = None
        popen.stdout = MagicMock()
        popen.stdout.readline.return_value = ""
        client = StdioClient(popen, mcp_server_id="test")
        return client, popen

    def test_no_id_is_written(self) -> None:
        client, popen = self._client()

        client.notify("notifications/initialized")

        written = json.loads(popen.stdin.write.call_args.args[0])
        assert "id" not in written
        assert written["method"] == "notifications/initialized"

    def test_a_legacy_connection_is_not_sent_the_modern_envelope(self) -> None:
        client, popen = self._client()
        client.modern_envelope = False

        client.notify("notifications/initialized")

        written = json.loads(popen.stdin.write.call_args.args[0])
        assert _PV not in written["params"]["_meta"]

    def test_a_write_failure_raises(self) -> None:
        client, popen = self._client()
        popen.stdin.write.side_effect = BrokenPipeError("gone")

        with pytest.raises(ClientError, match="write_failed"):
            client.notify("notifications/initialized")
