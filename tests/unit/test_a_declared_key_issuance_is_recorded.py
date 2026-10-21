"""A key-pinned server may declare where its keys come from, and the call records the declaration (#1953).

ADR-030 §2 and §6, T27. `tls.peer_identity.issuance` is one of a closed set
(`kbs-attested`). It is copied from the configuration onto the invocation
events as `declared_key_issuance`, and every export format but CEF, which has
no free field for it, carries it. It is a declaration: nothing names it
verified, and it changes neither the identity digest pins bind to nor what the
handshake accepts.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.compliance import CEFExporter, JSONLinesExporter, LEEFExporter, SyslogExporter
from mcp_hangar.domain.events import ToolInvocationCompleted, ToolInvocationFailed
from mcp_hangar.domain.exceptions import ToolInvocationError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.value_objects.peer_identity import KEY_ISSUANCES, PeerIdentity
from mcp_hangar.infrastructure.observability.otlp_audit_exporter import _peer_key_attributes
from mcp_hangar.observability.conventions import Upstream

PIN = "sha256:" + "a" * 64
_TOOLS = {"result": {"tools": [{"name": "read", "description": "reads", "inputSchema": {"type": "object"}}]}}


def _identity(**extra: object) -> dict[str, object]:
    return {"key_ref": "payments-tls", "spki_sha256": [PIN], **extra}


def _ready(tls: dict[str, object] | None) -> tuple[McpServer, MagicMock]:
    server = McpServer(mcp_server_id="payments", mode="remote", endpoint="https://upstream:8443", tls=tls)
    client = MagicMock()
    client.is_alive.return_value = True
    client.call.return_value = _TOOLS
    client.served_peer_spki.return_value = PIN if tls else None
    with patch.object(server, "_create_client", return_value=client):
        server.ensure_ready()
    server.collect_events()
    return server, client


class TestTheDeclaration:
    def test_the_closed_set_is_kbs_attested(self) -> None:
        assert frozenset({"kbs-attested"}) == KEY_ISSUANCES

    def test_it_is_read_from_the_peer_identity(self) -> None:
        assert PeerIdentity.from_mapping(_identity(issuance="kbs-attested")).issuance == "kbs-attested"

    def test_it_is_optional(self) -> None:
        assert PeerIdentity.from_mapping(_identity()).issuance is None

    @pytest.mark.parametrize("issuance", ["self-signed", "KBS-ATTESTED", "", 1, ["kbs-attested"]])
    def test_a_value_outside_the_closed_set_is_refused(self, issuance: object) -> None:
        with pytest.raises(ValueError, match="is not a declared key issuance"):
            PeerIdentity.from_mapping(_identity(issuance=issuance))

    def test_it_does_not_change_the_identity_digest_pins_bind_to(self) -> None:
        plain = McpServer(mcp_server_id="payments", mode="remote", tls={"peer_identity": _identity()})
        declared = McpServer(
            mcp_server_id="payments", mode="remote", tls={"peer_identity": _identity(issuance="kbs-attested")}
        )

        assert declared.upstream_identity() == plain.upstream_identity() == "peer-key:payments-tls"

    def test_it_does_not_change_the_keys_the_handshake_accepts(self) -> None:
        plain = PeerIdentity.from_mapping(_identity())
        declared = PeerIdentity.from_mapping(_identity(issuance="kbs-attested"))

        assert declared.spki_sha256 == plain.spki_sha256


class TestTheCallRecordsIt:
    def test_a_served_call(self) -> None:
        server, _ = _ready({"peer_identity": _identity(issuance="kbs-attested")})

        server.invoke_tool("read", {})

        (completed,) = [e for e in server.collect_events() if isinstance(e, ToolInvocationCompleted)]
        assert completed.declared_key_issuance == "kbs-attested"

    def test_a_failed_call(self) -> None:
        server, client = _ready({"peer_identity": _identity(issuance="kbs-attested")})
        client.call.return_value = {"error": {"code": -32000, "message": "boom"}}

        with pytest.raises(ToolInvocationError):
            server.invoke_tool("read", {})

        (failed,) = [e for e in server.collect_events() if isinstance(e, ToolInvocationFailed)]
        assert failed.declared_key_issuance == "kbs-attested"

    @pytest.mark.parametrize("tls", [{"peer_identity": _identity()}, None], ids=["no-issuance", "not-key-pinned"])
    def test_a_server_that_declares_none_records_none(self, tls) -> None:
        server, _ = _ready(tls)

        server.invoke_tool("read", {})

        (completed,) = [e for e in server.collect_events() if isinstance(e, ToolInvocationCompleted)]
        assert completed.declared_key_issuance is None

    def test_an_event_written_before_the_field_reads_back(self) -> None:
        assert ToolInvocationCompleted(mcp_server_id="s").declared_key_issuance is None


def _line(exporter_cls, peer_key) -> str:
    lines: list[str] = []
    exporter_cls(output_fn=lines.append).export_tool_invocation("srv", "echo", "success", 1.0, peer_key=peer_key)
    (line,) = lines
    return line


_DECLARED = {"upstream_peer_spki": PIN, "declared_key_issuance": "kbs-attested"}


class TestTheExportsCarryIt:
    def test_json_lines(self) -> None:
        assert json.loads(_line(JSONLinesExporter, _DECLARED))["declared_key_issuance"] == "kbs-attested"

    @pytest.mark.parametrize("exporter_cls", [LEEFExporter, SyslogExporter])
    def test_leef_and_syslog(self, exporter_cls) -> None:
        assert "declaredKeyIssuance" in _line(exporter_cls, _DECLARED)
        assert "kbs-attested" in _line(exporter_cls, _DECLARED)

    def test_otlp(self) -> None:
        assert _peer_key_attributes(_DECLARED)[Upstream.DECLARED_KEY_ISSUANCE] == "kbs-attested"

    def test_cef_has_no_field_for_it_and_invents_none(self) -> None:
        line = _line(CEFExporter, _DECLARED)

        assert "kbs-attested" not in line
        assert "fileType=UpstreamTlsKeyVerified" in line

    @pytest.mark.parametrize("exporter_cls", [JSONLinesExporter, LEEFExporter, SyslogExporter])
    def test_none_is_invented_when_none_is_declared(self, exporter_cls) -> None:
        line = _line(exporter_cls, {"upstream_peer_spki": PIN})

        assert "issuance" not in line.lower()


class TestNothingCallsItVerified:
    def test_every_name_it_goes_by_says_declared(self) -> None:
        from mcp_hangar.compliance.refusal import PEER_KEY_FIELDS

        names = [wire for key, wire in PEER_KEY_FIELDS if key == "declared_key_issuance"]
        names += ["declared_key_issuance", Upstream.DECLARED_KEY_ISSUANCE]

        assert names and all("declared" in name.lower() and "verif" not in name.lower() for name in names)


def test_the_core_advertises_that_it_records_the_declaration(monkeypatch) -> None:
    # The operator registers a server that declares `issuance` only with a core
    # listing this; an older one refuses the key at load.
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from mcp_hangar.server.api import system

    monkeypatch.setattr(system, "dispatch_query", AsyncMock(return_value=SimpleNamespace(to_dict=lambda: {})))

    response = asyncio.run(system.get_system_info(SimpleNamespace()))

    assert "declared_key_issuance" in json.loads(response.body)["system"]["capabilities"]
