"""A refused upstream key reaches the caller as itself, through every layer of a start (#1939).

ADR-030 §5. Before this, each layer converted whatever it caught: `HttpClient.call`
into `ClientError("request_failed")`, the aggregate's start into
`McpServerStartError`, a waiter into a fresh `McpServerStartError`, and the
`notifications/initialized` sender swallowed everything. Any one of them turns a
refusal with a reason into a start failure without one.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.domain.exceptions import UpstreamPeerKeyMismatchError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.http_client import HttpClient, HttpClientConfig
from mcp_hangar.retry import RetryPolicy, should_retry
from tests.unit.test_an_upstream_is_pinned_by_its_key import _leaf, _Upstream

_OTHER_KEY = "sha256:" + "b" * 64


def _remote(port: int) -> McpServer:
    return McpServer(mcp_server_id="pinned", mode="remote", endpoint=f"https://127.0.0.1:{port}")


def _pinned_client(port: int, pins: frozenset[str]) -> HttpClient:
    return HttpClient(
        endpoint=f"https://127.0.0.1:{port}",
        http_config=HttpClientConfig(peer_key_pins=pins, max_retries=0),
        mcp_server_id="pinned",
    )


class TestTheClientDoesNotWrapTheRefusal:
    def test_a_call_to_an_unlisted_key_raises_the_refusal_itself(self, tmp_path) -> None:
        cert, key, _ = _leaf(tmp_path, "upstream")
        upstream = _Upstream(cert, key)
        client = _pinned_client(upstream.port, frozenset({_OTHER_KEY}))
        try:
            with pytest.raises(UpstreamPeerKeyMismatchError):
                client.call("initialize", {})
        finally:
            client.close()
            upstream.stop()

        assert upstream.received == b""


class TestTheStartFailsWithTheRefusal:
    def test_a_cold_start_against_an_unlisted_key_raises_the_refusal_not_a_start_error(self, tmp_path) -> None:
        cert, key, _ = _leaf(tmp_path, "upstream")
        upstream = _Upstream(cert, key)
        server = _remote(upstream.port)
        client = _pinned_client(upstream.port, frozenset({_OTHER_KEY}))
        try:
            with (
                patch.object(server, "_create_client", return_value=client),
                pytest.raises(UpstreamPeerKeyMismatchError),
            ):
                server.ensure_ready()
        finally:
            upstream.stop()

        assert upstream.received == b""
        assert isinstance(server._start_error, UpstreamPeerKeyMismatchError)
        assert server.state.value != "ready"

    def test_a_caller_waiting_on_that_start_receives_the_same_refusal(self) -> None:
        server = McpServer(mcp_server_id="pinned", mode="remote", endpoint="https://upstream:8443")
        raised: list[BaseException] = []

        def refused_after_a_moment() -> None:
            time.sleep(0.1)
            raise UpstreamPeerKeyMismatchError(_OTHER_KEY)

        def start() -> None:
            try:
                server.ensure_ready()
            except Exception as error:  # noqa: BLE001 -- collected for the assertion
                raised.append(error)

        with patch.object(server, "_create_client", side_effect=refused_after_a_moment):
            starter = threading.Thread(target=start)
            starter.start()
            time.sleep(0.05)
            waiter = threading.Thread(target=start)
            waiter.start()
            starter.join(timeout=10)
            waiter.join(timeout=10)

        assert [type(error) for error in raised] == [UpstreamPeerKeyMismatchError] * 2

    def test_a_refusal_on_the_initialized_notification_fails_the_start(self) -> None:
        server = McpServer(mcp_server_id="pinned", mode="remote", endpoint="https://upstream:8443")
        client = MagicMock()
        client.is_alive.return_value = True
        client.call.return_value = {"result": {"tools": [], "protocolVersion": "2025-06-18", "capabilities": {}}}
        client.notify.side_effect = UpstreamPeerKeyMismatchError(_OTHER_KEY)

        with patch.object(server, "_create_client", return_value=client), pytest.raises(UpstreamPeerKeyMismatchError):
            server.ensure_ready()

        assert server.state.value != "ready"

    def test_any_other_notification_failure_still_does_not_fail_the_start(self) -> None:
        server = McpServer(mcp_server_id="pinned", mode="remote", endpoint="https://upstream:8443")
        client = MagicMock()
        client.is_alive.return_value = True
        client.call.return_value = {"result": {"tools": [], "protocolVersion": "2025-06-18", "capabilities": {}}}
        client.notify.side_effect = RuntimeError("the upstream mishandled the notification")

        with patch.object(server, "_create_client", return_value=client):
            server.ensure_ready()

        assert server.state.value == "ready"


class TestTheRefusalIsNeverRetried:
    def test_no_retry_policy_retries_it(self) -> None:
        everything = RetryPolicy(max_attempts=5, retry_on=["Error", "key", "upstream"])

        assert should_retry(UpstreamPeerKeyMismatchError(_OTHER_KEY), everything) is False
