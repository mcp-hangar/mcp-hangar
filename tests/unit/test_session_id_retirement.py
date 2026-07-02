"""SEP-2567: the transport ``Mcp-Session-Id`` handling is deprecated and guarded.

These tests pin the fail-safe behavior:

- A relay to a stateless upstream carries NO ``Mcp-Session-Id`` header.
- Declaring the upstream stateless (``stateless_upstream=True``) suppresses the
  transport session id entirely (never captured, never echoed).
- Backward-compat is preserved: a legacy session-based upstream that returns
  ``Mcp-Session-Id`` on one call still has it echoed on the next.
- The audit/correlation ``session_id`` (``CallerIdentity.session_id``) is a
  SEPARATE concept and still flows to the compliance exporters, untouched.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from mcp_hangar.http_client import AuthConfig, HttpClient, HttpClientConfig


def _make_capture(captured: dict[str, Any], response_headers: dict[str, str] | None = None):
    """Return a fake ``httpx.Client.post`` that records the outbound headers."""

    def capture_post(url, *, json=None, timeout=None, headers=None, **kwargs):
        captured["headers"] = dict(headers) if headers else {}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        resp_headers = {"Content-Type": "application/json"}
        if response_headers:
            resp_headers.update(response_headers)
        mock_resp.headers = resp_headers
        mock_resp.json.return_value = {"jsonrpc": "2.0", "id": "test", "result": {"ok": True}}
        return mock_resp

    return capture_post


class TestStatelessUpstreamCarriesNoSessionId:
    def test_stateless_upstream_relay_sends_no_mcp_session_id(self) -> None:
        """A relay to a stateless upstream never emits a transport Mcp-Session-Id."""
        client = HttpClient(
            endpoint="http://stateless-upstream:8080",
            auth_config=AuthConfig(),
            http_config=HttpClientConfig(),
        )
        captured: dict[str, Any] = {}
        # Stateless upstream: no Mcp-Session-Id in the response headers.
        with patch.object(client._client, "post", side_effect=_make_capture(captured)):
            client.call("tools/call", {"name": "t", "arguments": {}})

        assert "Mcp-Session-Id" not in captured["headers"]
        assert client._mcp_session_id is None

    def test_stateless_flag_suppresses_capture_and_echo(self) -> None:
        """With stateless_upstream=True, a returned session id is neither captured nor echoed."""
        client = HttpClient(
            endpoint="http://declared-stateless:8080",
            auth_config=AuthConfig(),
            http_config=HttpClientConfig(stateless_upstream=True),
        )
        captured: dict[str, Any] = {}
        # Even if a (misbehaving) upstream returns a session id, we must ignore it.
        post = _make_capture(captured, response_headers={"Mcp-Session-Id": "sess-abc"})
        with patch.object(client._client, "post", side_effect=post):
            client.call("initialize", {})
            assert client._mcp_session_id is None  # not captured
            client.call("tools/call", {"name": "t", "arguments": {}})

        assert "Mcp-Session-Id" not in captured["headers"]  # not echoed


class TestLegacySessionBasedUpstreamBackwardCompat:
    def test_session_based_upstream_still_echoes_established_session(self) -> None:
        """Backward-compat: a legacy upstream that establishes a session gets it echoed back."""
        client = HttpClient(
            endpoint="http://legacy-session-upstream:8080",
            auth_config=AuthConfig(),
            http_config=HttpClientConfig(),  # default: not declared stateless
        )

        # First call: upstream establishes a session via the response header.
        first: dict[str, Any] = {}
        post1 = _make_capture(first, response_headers={"Mcp-Session-Id": "sess-xyz"})
        with patch.object(client._client, "post", side_effect=post1):
            client.call("initialize", {})

        assert client._mcp_session_id == "sess-xyz"
        assert "Mcp-Session-Id" not in first["headers"]  # nothing to echo yet

        # Second call: the established session id must be echoed.
        second: dict[str, Any] = {}
        with patch.object(client._client, "post", side_effect=_make_capture(second)):
            client.call("tools/call", {"name": "t", "arguments": {}})

        assert second["headers"].get("Mcp-Session-Id") == "sess-xyz"


class TestAuditSessionIdUnaffected:
    def test_audit_session_id_still_flows_to_exporter(self) -> None:
        """The audit/correlation session_id is a separate field and reaches the exporter."""
        from mcp_hangar.compliance.jsonlines_exporter import JSONLinesExporter

        lines: list[str] = []
        exporter = JSONLinesExporter(output_fn=lines.append)

        exporter.export_tool_invocation(
            mcp_server_id="srv-1",
            tool_name="do_thing",
            status="success",
            duration_ms=12.0,
            user_id="user-1",
            session_id="audit-corr-123",
        )

        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["session_id"] == "audit-corr-123"
