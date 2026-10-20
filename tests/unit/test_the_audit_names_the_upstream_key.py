"""The audit trail names the TLS key of the connection that served, or was refused, a call (#1942).

ADR-030 §6. The key is read from the socket of the connection that served the
attempt, never from configuration; a refused peer's key is kept as observed,
apart from the verified one; and every export format carries the fields.
"""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.application.event_handlers.audit_event_handler import OTLPAuditEventHandler
from mcp_hangar.compliance import CEFExporter, JSONLinesExporter, LEEFExporter, SyslogExporter
from mcp_hangar.domain.events import ToolCallRefused, ToolInvocationCompleted, ToolInvocationFailed
from mcp_hangar.domain.exceptions import ToolInvocationError, UpstreamPeerKeyMismatchError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.http_client import HttpClient, HttpClientConfig
from mcp_hangar.infrastructure.observability.otlp_audit_exporter import _peer_key_attributes
from mcp_hangar.observability.conventions import Upstream
from tests.unit.test_an_upstream_is_pinned_by_its_key import _leaf, _Upstream

VERIFIED = "sha256:" + "1" * 64
OBSERVED = "sha256:" + "2" * 64
_REFUSED = {"upstream_peer_spki": None, "failure_code": "upstream_peer_key_mismatch", "observed_peer_spki": OBSERVED}
_TOOLS = {"result": {"tools": [{"name": "read", "description": "reads", "inputSchema": {"type": "object"}}]}}


def _ready(served: str | None) -> tuple[McpServer, MagicMock]:
    server = McpServer(mcp_server_id="pinned", mode="remote", endpoint="https://upstream:8443")
    client = MagicMock()
    client.is_alive.return_value = True
    client.call.return_value = _TOOLS
    client.served_peer_spki.return_value = served
    with patch.object(server, "_create_client", return_value=client):
        server.ensure_ready()
    server.collect_events()
    return server, client


class TestTheKeyIsReadFromTheServingSocket:
    def test_the_client_remembers_the_key_of_the_connection_that_served(self, tmp_path) -> None:
        cert, key, pin = _leaf(tmp_path, "upstream")
        upstream = _Upstream(cert, key)
        client = HttpClient(
            endpoint=f"https://127.0.0.1:{upstream.port}",
            http_config=HttpClientConfig(peer_key_pins=frozenset({pin}), max_retries=0),
        )
        try:
            client.call("tools/list", {})
            served = client.served_peer_spki()
        finally:
            client.close()
            upstream.stop()

        assert served == pin

    def test_it_is_per_thread(self, tmp_path) -> None:
        cert, key, pin = _leaf(tmp_path, "upstream")
        upstream = _Upstream(cert, key)
        client = HttpClient(
            endpoint=f"https://127.0.0.1:{upstream.port}",
            http_config=HttpClientConfig(peer_key_pins=frozenset({pin}), max_retries=0),
        )
        seen_elsewhere: list[str | None] = []
        try:
            client.call("tools/list", {})
            other = threading.Thread(target=lambda: seen_elsewhere.append(client.served_peer_spki()))
            other.start()
            other.join(timeout=5)
        finally:
            client.close()
            upstream.stop()

        assert seen_elsewhere == [None]

    def test_a_server_without_a_key_list_records_no_key(self) -> None:
        server, _ = _ready(None)
        server.invoke_tool("read", {})

        (completed,) = [e for e in server.collect_events() if isinstance(e, ToolInvocationCompleted)]
        assert completed.upstream_peer_spki is None


class TestTheEventsCarryTheKey:
    def test_a_served_call_records_the_verified_key(self) -> None:
        server, _ = _ready(VERIFIED)
        server.invoke_tool("read", {})

        (completed,) = [e for e in server.collect_events() if isinstance(e, ToolInvocationCompleted)]
        assert completed.upstream_peer_spki == VERIFIED

    def test_an_application_failure_on_a_verified_connection_records_the_key(self) -> None:
        server, client = _ready(VERIFIED)
        client.call.return_value = {"error": {"code": -32603, "message": "boom"}}

        with pytest.raises(ToolInvocationError):
            server.invoke_tool("read", {})

        (failed,) = [e for e in server.collect_events() if isinstance(e, ToolInvocationFailed)]
        assert (failed.upstream_peer_spki, failed.failure_code, failed.observed_peer_spki) == (VERIFIED, None, None)

    def test_a_refused_key_is_observed_and_never_recorded_as_verified(self) -> None:
        server, client = _ready(VERIFIED)
        client.call.side_effect = UpstreamPeerKeyMismatchError(OBSERVED)
        client.served_peer_spki.return_value = None

        with pytest.raises(UpstreamPeerKeyMismatchError):
            server.invoke_tool("read", {})

        (failed,) = [e for e in server.collect_events() if isinstance(e, ToolInvocationFailed)]
        assert failed.upstream_peer_spki is None
        assert failed.failure_code == "upstream_peer_key_mismatch"
        assert failed.observed_peer_spki == OBSERVED

    def test_events_written_before_the_fields_read_back(self) -> None:
        old = ToolInvocationFailed(mcp_server_id="s", tool_name="t", error_type="TimeoutError")

        assert (old.upstream_peer_spki, old.failure_code, old.observed_peer_spki) == (None, None, None)


class TestTheAuditHandlerPassesTheFieldsOn:
    @pytest.mark.parametrize(
        ("event", "expected"),
        [
            (
                ToolInvocationCompleted(mcp_server_id="s", tool_name="t", upstream_peer_spki=VERIFIED),
                {"upstream_peer_spki": VERIFIED},
            ),
            (
                ToolInvocationFailed(
                    mcp_server_id="s",
                    tool_name="t",
                    error_type="UpstreamPeerKeyMismatchError",
                    failure_code="upstream_peer_key_mismatch",
                    observed_peer_spki=OBSERVED,
                ),
                _REFUSED,
            ),
            (
                ToolCallRefused(mcp_server_id="s", tool_name="t", observed_peer_spki=OBSERVED),
                {"observed_peer_spki": OBSERVED},
            ),
        ],
    )
    def test_each_event_reaches_the_exporter_with_its_key(self, event, expected) -> None:
        exporter = MagicMock()
        OTLPAuditEventHandler(audit_exporter=exporter).handle(event)

        assert exporter.export_tool_invocation.call_args.kwargs["peer_key"] == expected


def _line(exporter_cls, peer_key) -> str:
    lines: list[str] = []
    exporter_cls(output_fn=lines.append).export_tool_invocation("srv", "echo", "error", 1.0, peer_key=peer_key)
    (line,) = lines
    return line


class TestEveryFormatCarriesTheFields:
    def test_json_lines(self) -> None:
        record = json.loads(_line(JSONLinesExporter, _REFUSED))

        assert record["failure_code"] == "upstream_peer_key_mismatch"
        assert record["observed_peer_spki"] == OBSERVED
        assert "upstream_peer_spki" not in record

    @pytest.mark.parametrize("exporter_cls", [LEEFExporter, SyslogExporter])
    def test_leef_and_syslog(self, exporter_cls) -> None:
        line = _line(exporter_cls, _REFUSED)

        assert "failureCode" in line and "upstream_peer_key_mismatch" in line
        assert "observedPeerSpki" in line and OBSERVED in line
        assert "upstreamPeerSpki" not in line

    def test_cef_writes_the_one_key_as_file_hash_and_says_which(self) -> None:
        verified = _line(CEFExporter, {"upstream_peer_spki": VERIFIED})
        refused = _line(CEFExporter, _REFUSED)

        assert f"fileHash={VERIFIED}" in verified and "fileType=UpstreamTlsKeyVerified" in verified
        assert f"fileHash={OBSERVED}" in refused and "fileType=UpstreamTlsKeyRefused" in refused

    @pytest.mark.parametrize("exporter_cls", [JSONLinesExporter, LEEFExporter, SyslogExporter, CEFExporter])
    def test_a_call_without_a_key_gets_none_invented(self, exporter_cls) -> None:
        line = _line(exporter_cls, None)

        assert all(marker not in line for marker in ("peer_spki", "PeerSpki", "failure", "fileHash"))

    def test_otlp_attributes(self) -> None:
        assert _peer_key_attributes(_REFUSED) == {
            Upstream.FAILURE_CODE: "upstream_peer_key_mismatch",
            Upstream.OBSERVED_PEER_SPKI: OBSERVED,
        }
