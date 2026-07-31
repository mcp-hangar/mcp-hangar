"""Outbound per-request protocol context in `_meta` (WS-1 #291).

Stateless upstreams (SEP-2575) have no initialize handshake, so the protocol
version + client info travel in every request's `params._meta`.
"""

from unittest.mock import MagicMock, patch

from mcp_hangar.protocol import (
    HANGAR_CLIENT_INFO,
    SUPPORTED_PROTOCOL_VERSION,
    inject_protocol_meta,
)

_PV = "io.modelcontextprotocol/protocolVersion"
_CI = "io.modelcontextprotocol/clientInfo"


def test_injects_protocol_version_and_client_info_into_empty_params() -> None:
    out = inject_protocol_meta({"name": "x"})

    assert out["_meta"][_PV] == SUPPORTED_PROTOCOL_VERSION == "2026-07-28"
    assert out["_meta"][_CI] == HANGAR_CLIENT_INFO
    assert out["name"] == "x"


def test_preserves_existing_meta_keys() -> None:
    out = inject_protocol_meta({"_meta": {"traceparent": "abc"}})

    assert out["_meta"]["traceparent"] == "abc"
    assert out["_meta"][_PV] == SUPPORTED_PROTOCOL_VERSION


def test_caller_set_protocol_version_wins() -> None:
    out = inject_protocol_meta({"_meta": {_PV: "2099-01-01"}})

    assert out["_meta"][_PV] == "2099-01-01"


def test_does_not_mutate_caller_params() -> None:
    params: dict = {"name": "x"}

    inject_protocol_meta(params)

    assert "_meta" not in params


def test_client_info_is_copied_not_shared() -> None:
    out = inject_protocol_meta({})

    out["_meta"][_CI]["name"] = "mutated"
    assert HANGAR_CLIENT_INFO["name"] == "mcp-registry"


def test_http_client_call_injects_protocol_meta_on_outbound_request() -> None:
    from mcp_hangar.http_client import AuthConfig, HttpClient, HttpClientConfig

    client = HttpClient(
        endpoint="http://upstream:8080",
        auth_config=AuthConfig(),
        http_config=HttpClientConfig(),
    )

    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "application/json"}
    resp.json.return_value = {"jsonrpc": "2.0", "id": "x", "result": {}}

    with patch.object(client._client, "post", return_value=resp) as mock_post:
        client.call("tools/call", {"name": "t", "arguments": {}})

    body = mock_post.call_args.kwargs["json"]
    assert body["params"]["_meta"][_PV] == "2026-07-28"
    assert body["params"]["name"] == "t"
