"""An upstream restart must not wedge the gateway permanently (#651).

Streamable HTTP answers a request carrying an unknown `Mcp-Session-Id` with 404,
and the resolution is to establish a new session. Nothing did: the id was
captured once and never cleared, 404 was not in `retry_status_codes`, and the
caller saw an opaque `HTTP error: 404`.

So after any upstream pod restart every call failed, forever -- while
`/health/ready` still reported `{"status": "healthy", "ready_mcp_servers": 1}`,
so nothing restarted the pod and nothing alerted. Found on kind: recovery
required restarting the *gateway*, not the upstream.

Two halves, tested separately because they live in different layers:

* the client stops presenting a dead session and says so distinguishably;
* the domain re-handshakes once and retries.

The health assertions are the part worth reading. A session lost to an ordinary
restart is not evidence of an unhealthy upstream, so a *successful*
renegotiation records nothing -- marking it unhealthy would pull the pod out of
its Service for something that just healed itself (adjacent to #599). A *failed*
renegotiation does record a failure, because at that point the upstream will not
hold a session.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx

from mcp_hangar.http_client import HttpClient
from mcp_hangar.protocol import SESSION_TERMINATED_CODE, SESSION_TERMINATED_REASON


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None, body: str = "{}"):
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self.text = body
        self.content = body.encode()

    def json(self) -> Any:
        import json

        return json.loads(self.text)


class TestClientDropsTheDeadSession:
    def _client_with_session(self, session_id: str = "sess-1") -> HttpClient:
        client = HttpClient("http://upstream.invalid/mcp", mcp_server_id="probe")
        client._mcp_session_id = session_id
        return client

    def test_404_with_a_session_clears_it(self) -> None:
        client = self._client_with_session()
        client._client = MagicMock()
        client._client.post.return_value = _FakeResponse(404, body="Not Found")

        client.call("tools/list", {})

        assert client._mcp_session_id is None, "the dead session id was kept and would be resent"

    def test_404_with_a_session_is_reported_distinguishably(self) -> None:
        """A generic `HTTP error: 404` gave the caller nothing to act on."""
        client = self._client_with_session()
        client._client = MagicMock()
        client._client.post.return_value = _FakeResponse(404, body="Not Found")

        response = client.call("tools/list", {})

        assert response["error"]["code"] == SESSION_TERMINATED_CODE
        assert response["error"]["data"]["reason"] == SESSION_TERMINATED_REASON

    def test_404_without_a_session_stays_a_plain_http_error(self) -> None:
        """No session means 404 is "no such endpoint", not "session terminated".

        The SDK client draws the same distinction. Collapsing them would make a
        wrong URL look like a recoverable condition and retry it forever.
        """
        client = HttpClient("http://upstream.invalid/mcp", mcp_server_id="probe")
        client._client = MagicMock()
        client._client.post.return_value = _FakeResponse(404, body="Not Found")

        response = client.call("tools/list", {})

        assert response["error"]["code"] == -32000
        assert "HTTP error: 404" in response["error"]["message"]


def _server_with(client: Any):
    """A minimally-constructed McpServer wired to *client*."""
    from mcp_hangar.domain.model.mcp_server import McpServer

    server = object.__new__(McpServer)
    # `mcp_server_id` is a read-only property over `_id`; set the backing field.
    server._id = "upstream"
    server._health = MagicMock()
    server._perform_mcp_handshake = MagicMock()
    return server


TERMINATED = {
    "error": {
        "code": SESSION_TERMINATED_CODE,
        "message": "Session terminated",
        "data": {"reason": SESSION_TERMINATED_REASON},
    }
}
OK = {"result": {"content": []}}


class TestDomainRenegotiates:
    def test_a_terminated_session_is_renegotiated_and_the_call_retried(self) -> None:
        client = MagicMock()
        client.call.side_effect = [TERMINATED, OK]
        server = _server_with(client)

        result = server._call_with_session_recovery(client, "tools/call", {"name": "t"})

        assert result == OK
        server._perform_mcp_handshake.assert_called_once_with(client)
        assert client.call.call_count == 2

    def test_a_successful_renegotiation_records_no_health_failure(self) -> None:
        """The load-bearing one.

        Recording a failure here would count an ordinary upstream restart against
        the upstream, and enough of them would pull a working pod out of its
        Service.
        """
        client = MagicMock()
        client.call.side_effect = [TERMINATED, OK]
        server = _server_with(client)

        server._call_with_session_recovery(client, "tools/call", {"name": "t"})

        server._health.record_failure.assert_not_called()

    def test_a_failed_renegotiation_records_a_health_failure(self) -> None:
        client = MagicMock()
        client.call.side_effect = [TERMINATED]
        server = _server_with(client)
        server._perform_mcp_handshake.side_effect = RuntimeError("upstream refused initialize")

        result = server._call_with_session_recovery(client, "tools/call", {"name": "t"})

        server._health.record_failure.assert_called_once()
        assert result == TERMINATED, "the original error must still reach the caller"

    def test_the_retry_happens_at_most_once(self) -> None:
        """A second failure is an upstream that will not hold a session.

        Looping would turn a recoverable blip into unbounded retries against a
        sick backend.
        """
        client = MagicMock()
        client.call.side_effect = [TERMINATED, TERMINATED]
        server = _server_with(client)

        result = server._call_with_session_recovery(client, "tools/call", {"name": "t"})

        assert client.call.call_count == 2
        assert server._perform_mcp_handshake.call_count == 1
        assert result == TERMINATED

    def test_an_ordinary_response_is_passed_straight_through(self) -> None:
        client = MagicMock()
        client.call.return_value = OK
        server = _server_with(client)

        assert server._call_with_session_recovery(client, "tools/call", {"name": "t"}) == OK
        server._perform_mcp_handshake.assert_not_called()

    def test_an_unrelated_error_is_not_mistaken_for_a_dead_session(self) -> None:
        client = MagicMock()
        client.call.return_value = {"error": {"code": -32000, "message": "HTTP error: 500"}}
        server = _server_with(client)

        server._call_with_session_recovery(client, "tools/call", {"name": "t"})

        server._perform_mcp_handshake.assert_not_called()
