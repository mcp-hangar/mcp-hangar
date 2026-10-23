"""A refused upstream key on a warm server reaches the caller as itself (#1940).

ADR-030 §5. A READY server's calls go through `invoke_tool`, `relay_request`, the
refresh inside `invoke_tool` and session renegotiation; each converted a
transport refusal into `ToolInvocationError`, a missing tool, or a stale 404.
The background probes -- the health check and the catalogue re-check -- have no
caller to refuse: they record the failure and keep their boolean contract,
because an exception escaping them leaves a broken upstream READY (#1698).
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.domain.events import HealthCheckFailed, ToolInvocationFailed
from mcp_hangar.domain.exceptions import ToolInvocationError, ToolNotFoundError, UpstreamPeerKeyMismatchError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.http_client import HttpClient, HttpClientConfig
from mcp_hangar.protocol import SESSION_TERMINATED_CODE, SESSION_TERMINATED_REASON
from tests.unit.test_an_upstream_is_pinned_by_its_key import _leaf, _Upstream

_KEY = "sha256:" + "c" * 64
_TOOLS = {"result": {"tools": [{"name": "read", "description": "reads", "inputSchema": {"type": "object"}}]}}


def _ready() -> tuple[McpServer, MagicMock]:
    """A READY remote server on a mock client that listed one tool."""
    server = McpServer(mcp_server_id="pinned", mode="remote", endpoint="https://upstream:8443")
    client = MagicMock()
    client.is_alive.return_value = True
    client.call.return_value = _TOOLS
    with patch.object(server, "_create_client", return_value=client):
        server.ensure_ready()
    server.collect_events()
    return server, client


def _refused(*_args: Any, **_kwargs: Any) -> Any:
    raise UpstreamPeerKeyMismatchError(_KEY)


class TestACallIsRefusedWithTheReason:
    def test_invoke_records_the_failure_and_raises_the_refusal_not_a_tool_error(self) -> None:
        server, client = _ready()
        client.call.side_effect = _refused

        with pytest.raises(UpstreamPeerKeyMismatchError):
            server.invoke_tool("read", {})

        failed = [e for e in server.collect_events() if isinstance(e, ToolInvocationFailed)]
        assert [e.error_type for e in failed] == ["UpstreamPeerKeyMismatchError"]
        assert server._health.consecutive_failures == 1

    def test_a_relayed_prompt_or_resource_raises_the_refusal_and_counts_it(self) -> None:
        server, client = _ready()
        client.call.side_effect = _refused

        with pytest.raises(UpstreamPeerKeyMismatchError):
            server.relay_request("prompts/get", {"name": "p"})

        assert server._health.consecutive_failures == 1

    def test_a_refresh_for_an_unlisted_tool_raises_the_refusal_not_tool_not_found(self) -> None:
        server, client = _ready()
        client.call.side_effect = _refused

        with pytest.raises(UpstreamPeerKeyMismatchError):
            server.invoke_tool("write", {})

    def test_renegotiating_a_lost_session_on_an_unlisted_key_raises_the_refusal(self) -> None:
        server, client = _ready()
        terminated = {"error": {"code": SESSION_TERMINATED_CODE, "data": {"reason": SESSION_TERMINATED_REASON}}}
        client.call.side_effect = [terminated, UpstreamPeerKeyMismatchError(_KEY)]

        with pytest.raises(UpstreamPeerKeyMismatchError):
            server.invoke_tool("read", {})

    def test_an_ordinary_transport_failure_is_still_a_tool_error(self) -> None:
        from mcp_hangar.domain.exceptions import ClientError

        server, client = _ready()
        client.call.side_effect = ClientError("connection_failed: reset")

        with pytest.raises(ToolInvocationError):
            server.invoke_tool("read", {})


class TestABackgroundProbeRecordsItAndRaisesNothing:
    def test_the_health_check_fails_degrades_and_names_the_refusal(self) -> None:
        server, client = _ready()
        client.call.side_effect = _refused

        assert server.health_check() is False

        failed = [e for e in server.collect_events() if isinstance(e, HealthCheckFailed)]
        assert len(failed) == 1
        assert server._health.consecutive_failures == 1

    def test_the_catalogue_recheck_keeps_its_catalogue(self) -> None:
        server, client = _ready()
        client.call.side_effect = _refused

        assert server.recheck_catalogue() is False
        assert server._tools.has("read")


class TestTheGetStreamStopsOnAnUnlistedKey:
    def test_the_stream_loop_returns_instead_of_retrying(self, tmp_path) -> None:
        cert, key, _ = _leaf(tmp_path, "upstream")
        upstream = _Upstream(cert, key)
        client = HttpClient(
            endpoint=f"https://127.0.0.1:{upstream.port}",
            http_config=HttpClientConfig(peer_key_pins=frozenset({_KEY}), max_retries=0),
        )
        client._sse_running = True
        try:
            # A retry would sleep before reconnecting: fail at the first one
            # rather than loop, so a regression is a failure and not a hang.
            with patch("mcp_hangar.http_client.time.sleep", side_effect=AssertionError("the stream retried")) as slept:
                client._notification_stream_loop(lambda message: None)
        finally:
            client.close()
            upstream.stop()

        slept.assert_not_called()
        assert upstream.received == b""


def test_tool_not_found_is_unchanged_when_the_listing_succeeds() -> None:
    server, _ = _ready()

    with pytest.raises(ToolNotFoundError):
        server.invoke_tool("write", {})
