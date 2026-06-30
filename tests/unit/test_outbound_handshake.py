"""Outbound MCP handshake uses a centralised protocol version, not a hardcoded one.

Guards the de-hardcoding done for WS-1 #339: the protocol version and clientInfo
Hangar advertises to upstream MCP servers must come from the module-level single
source of truth, so the stateless _meta negotiation / a version bump touch one place.
"""

from unittest.mock import MagicMock

from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.model.mcp_server import (
    HANGAR_CLIENT_INFO,
    SUPPORTED_PROTOCOL_VERSION,
)


def _fake_client() -> MagicMock:
    client = MagicMock()

    def _call(method: str, params: dict) -> dict:
        if method == "tools/list":
            return {"result": {"tools": []}}
        return {"result": {}}

    client.call.side_effect = _call
    return client


def test_outbound_handshake_advertises_centralised_protocol_version() -> None:
    server = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])
    client = _fake_client()

    server._perform_mcp_handshake(client)

    init_calls = [c for c in client.call.call_args_list if c.args[0] == "initialize"]
    assert len(init_calls) == 1
    params = init_calls[0].args[1]
    assert params["protocolVersion"] == SUPPORTED_PROTOCOL_VERSION
    assert params["clientInfo"] == HANGAR_CLIENT_INFO


def test_outbound_handshake_clientinfo_is_sent_as_a_copy() -> None:
    """Mutating the sent clientInfo must not mutate the shared module constant."""
    server = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])
    client = _fake_client()

    server._perform_mcp_handshake(client)

    sent = next(c for c in client.call.call_args_list if c.args[0] == "initialize").args[1]
    sent["clientInfo"]["name"] = "mutated"
    assert HANGAR_CLIENT_INFO["name"] == "mcp-registry"
