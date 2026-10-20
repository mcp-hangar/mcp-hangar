"""A remote server's HTTP metrics are labelled with its id, not its host (#1981).

The HTTP launcher built its client without the server's id, so every metric the
client labels -- `mcp_hangar_upstream_peer_key_connections_total` among them --
fell back to the endpoint's host. Two servers on one host shared a series, and a
dashboard keyed on the id saw nothing for a remote server.
"""

import pytest

from mcp_hangar import http_client
from mcp_hangar.domain.model import McpServer
from mcp_hangar.infrastructure.launchers import HttpLauncher

PIN = "sha256:" + "a" * 64


def test_the_launcher_gives_the_client_the_servers_id() -> None:
    client = HttpLauncher().launch(endpoint="https://203.0.113.5:8443/mcp", mcp_server_id="payments")
    try:
        assert client._mcp_server_id == "payments"
    finally:
        client.close()


def test_a_server_launches_its_client_under_its_own_id() -> None:
    server = McpServer(mcp_server_id="payments", mode="remote", endpoint="https://203.0.113.5:8443/mcp")

    client = server._create_client()
    try:
        assert client._mcp_server_id == "payments"
    finally:
        client.close()


def test_the_peer_key_handshake_count_is_labelled_with_the_id(monkeypatch: pytest.MonkeyPatch) -> None:
    labels: list[str] = []
    real = http_client._peer_key_context

    def recording(key_pins, metric_server=""):
        labels.append(metric_server)
        return real(key_pins, metric_server)

    monkeypatch.setattr(http_client, "_peer_key_context", recording)
    tls = {"peer_identity": {"key_ref": "payments-tls", "spki_sha256": [PIN]}}
    server = McpServer(mcp_server_id="payments", mode="remote", endpoint="https://203.0.113.5:8443/mcp", tls=tls)

    client = server._create_client()
    try:
        assert labels and set(labels) == {"payments"}
    finally:
        client.close()
