"""Outbound MCP startup handshake (WS-1 #339).

Covers:
- The advertised protocol version / clientInfo come from the centralised single
  source of truth and target the 2026-07-28 revision.
- Legacy upstreams (answer `initialize`) and stateless upstreams (SEP-2575,
  reply method-not-found) both complete the handshake.
- A genuine `initialize` error still fails startup.
"""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.domain.exceptions import McpServerStartError
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.model.mcp_server import (
    HANGAR_CLIENT_INFO,
    SUPPORTED_PROTOCOL_VERSION,
)

_METHOD_NOT_FOUND = -32601


def _client(init_resp: dict) -> MagicMock:
    """Fake MCP client: returns init_resp for initialize, empty tools otherwise."""
    client = MagicMock()
    client.process = None

    def _call(method: str, params: dict) -> dict:
        if method == "initialize":
            return init_resp
        return {"result": {"tools": []}}

    client.call.side_effect = _call
    return client


def _server() -> McpServer:
    return McpServer(mcp_server_id="test", mode="subprocess", command=["test"])


def _init_params(client: MagicMock) -> dict[str, Any]:
    call = next(c for c in client.call.call_args_list if c.args[0] == "initialize")
    return cast("dict[str, Any]", call.args[1])


def test_advertises_centralised_protocol_version_targeting_2026_07_28() -> None:
    client = _client({"result": {}})

    _server()._perform_mcp_handshake(client)

    params = _init_params(client)
    assert params["protocolVersion"] == SUPPORTED_PROTOCOL_VERSION == "2026-07-28"
    assert params["clientInfo"] == HANGAR_CLIENT_INFO


def test_clientinfo_is_sent_as_a_copy() -> None:
    client = _client({"result": {}})

    _server()._perform_mcp_handshake(client)

    _init_params(client)["clientInfo"]["name"] = "mutated"
    assert HANGAR_CLIENT_INFO["name"] == "mcp-registry"


def test_legacy_upstream_completes_and_discovers_tools() -> None:
    client = _client({"result": {}})

    _server()._perform_mcp_handshake(client)

    assert any(c.args[0] == "tools/list" for c in client.call.call_args_list)


def test_stateless_upstream_method_not_found_is_tolerated() -> None:
    """A stateless upstream (no initialize handler) must not fail startup."""
    client = _client({"error": {"code": _METHOD_NOT_FOUND, "message": "Method not found"}})

    _server()._perform_mcp_handshake(client)  # must not raise

    assert any(c.args[0] == "tools/list" for c in client.call.call_args_list)


def test_genuine_initialize_error_still_fails_startup() -> None:
    client = _client({"error": {"code": -32000, "message": "boom"}})

    with patch.object(McpServer, "_collect_startup_diagnostics", return_value={}):
        with pytest.raises(McpServerStartError):
            _server()._perform_mcp_handshake(client)
