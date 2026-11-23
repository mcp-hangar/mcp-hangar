"""Front-door ``resources/read`` of an unknown URI answers -32602 with ``data.uri`` (#2316).

SEP-2164 standardises resource-not-found on JSON-RPC ``-32602`` (Invalid Params)
with ``data: {"uri": <requested URI>}`` rather than ``-32002`` with no ``data``.
Verified both over the real Streamable HTTP ASGI surface on both protocol
revisions Hangar serves (``2025-11-25`` and ``2026-07-28``) and across the
handler's refusal/relay error branches.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from mcp_hangar._sdk_compat import INVALID_PARAMS, McpError
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.services.ui_resource_guard import (
    UiResourceGuard,
    reset_ui_resource_guard,
    set_ui_resource_guard,
)
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.domain.value_objects.ui_resource import UiResourcePolicy
from mcp_hangar.fastmcp_server import resource_link_read_through as rt
from mcp_hangar.fastmcp_server import tool_list_changed


def _identity(tenant_id: str | None) -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    rt._links.clear()
    reset_tool_access_resolver()
    reset_ui_resource_guard()
    tool_list_changed.reset()
    yield
    rt._links.clear()
    reset_tool_access_resolver()
    reset_ui_resource_guard()
    tool_list_changed.reset()


def _parse_mcp_response(response: httpx.Response) -> dict[str, Any]:
    """Extract the JSON-RPC payload from either JSON or SSE Streamable HTTP responses."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:") :].strip()
                if payload:
                    return json.loads(payload)
        raise AssertionError(f"No SSE data payload in response: {response.text!r}")
    return response.json()


def _post_mcp(
    app: Any,
    body: dict[str, Any],
    protocol_version: str,
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": protocol_version,
    }
    request_body = dict(body)
    if protocol_version == "2026-07-28":
        headers["MCP-Method"] = request_body["method"]
        params = dict(request_body.get("params") or {})
        if "uri" in params:
            headers["MCP-Name"] = params["uri"]
        params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        request_body["params"] = params
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        if protocol_version == "2025-11-25":
            init = client.post(
                "/mcp",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "0.0.1"},
                    },
                },
            )
            assert init.status_code == 200, init.text
        resp = client.post("/mcp", headers=headers, json=request_body)
        assert resp.status_code in (200, 400), resp.text
        return _parse_mcp_response(resp)


def _authenticated(app: Any, tenant_id: str = "tenant:a") -> Any:
    principal = Principal(id=PrincipalId("svc:test"), type=PrincipalType.SERVICE_ACCOUNT, tenant_id=tenant_id)
    auth = SimpleNamespace(principal=principal)

    async def _asgi(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})["auth"] = auth
        await app(scope, receive, send)

    return _asgi


def _front_door_app() -> Any:
    from mcp_hangar.server.bootstrap import build_serving_mcp_server
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    get_tool_access_resolver().set_topology_mode("front_door")
    server = build_serving_mcp_server()
    return _authenticated(mcp_app_for_serving(server))


class TestServedStreamableHttp:
    """Real ASGI Streamable HTTP wire checks on both protocol revisions."""

    @pytest.mark.parametrize("protocol_version", ["2025-11-25", "2026-07-28"])
    @pytest.mark.parametrize(
        "requested_uri",
        [
            "test://nonexistent-resource",
            "hangar://unprojected_server/demo://missing",
        ],
    )
    def test_unknown_uri_answers_invalid_params_with_data_uri(self, protocol_version: str, requested_uri: str) -> None:
        app = _front_door_app()
        with patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=["internal_secret_upstream"]):
            payload = _post_mcp(
                app,
                {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": requested_uri}},
                protocol_version=protocol_version,
            )

        assert "error" in payload, f"expected JSON-RPC error, got {payload!r}"
        error = payload["error"]
        assert error["code"] == INVALID_PARAMS == -32602
        assert error.get("data") == {"uri": requested_uri}
        assert "internal_secret_upstream" not in error.get("message", "")

    @pytest.mark.parametrize("protocol_version", ["2025-11-25", "2026-07-28"])
    def test_known_uri_is_unaffected(self, protocol_version: str) -> None:
        app = _front_door_app()
        with (
            patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=["server_a"]),
            patch.object(
                rt,
                "_relay_read",
                return_value={"result": {"contents": [{"uri": "demo://doc/1", "text": "hello"}]}},
            ),
        ):
            payload = _post_mcp(
                app,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "resources/read",
                    "params": {"uri": "hangar://server_a/demo://doc/1"},
                },
                protocol_version=protocol_version,
            )

        assert "result" in payload, f"expected JSON-RPC result, got {payload!r}"
        assert payload["result"]["contents"] == [{"uri": "hangar://server_a/demo://doc/1", "text": "hello"}]


class _FakeLow:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def add_request_handler(self, method: str, _params_type: Any, handler: Any) -> None:
        self.handlers[method] = handler


def _register() -> _FakeLow:
    low = _FakeLow()
    with (
        patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=True),
        patch("mcp_hangar.fastmcp_server.resource_link_read_through.lowlevel_server", return_value=low),
    ):
        assert rt.maybe_register_resource_read_through(MagicMock())
    return low


async def _call_read(low: _FakeLow, uri: str, tenant_id: str = "tenant:a") -> Any:
    token = identity_context_var.set(_identity(tenant_id))
    try:
        return await low.handlers["resources/read"](None, SimpleNamespace(uri=uri))
    finally:
        identity_context_var.reset(token)


class TestHandlerErrorBranches:
    @pytest.mark.asyncio
    async def test_denied_ui_resource_answers_invalid_params_with_data_uri(self) -> None:
        uri = "hangar://server_a/ui://widget/1"
        set_ui_resource_guard(UiResourceGuard({"tenant:a": UiResourcePolicy(allowlist=frozenset({"ui://widget/1"}))}))
        low = _register()
        with (
            patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=["server_a"]),
            pytest.raises(McpError) as excinfo,
        ):
            await _call_read(low, uri)

        assert excinfo.value.error.code == INVALID_PARAMS == -32602
        assert excinfo.value.error.data == {"uri": uri}

    @pytest.mark.asyncio
    async def test_missing_upstream_in_relay_read_answers_invalid_params_with_requested_uri(self) -> None:
        uri = "hangar://server_a/demo://doc/1"
        low = _register()
        fake_ctx = SimpleNamespace(get_mcp_server=lambda _id: None, get_group=lambda _id: None)
        with (
            patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=["server_a"]),
            patch("mcp_hangar.server.context.get_context", return_value=fake_ctx),
            pytest.raises(McpError) as excinfo,
        ):
            await _call_read(low, uri)

        assert excinfo.value.error.code == INVALID_PARAMS == -32602
        assert excinfo.value.error.data == {"uri": uri}

    @pytest.mark.asyncio
    async def test_upstream_not_found_rewrites_data_uri_to_requested_uri(self) -> None:
        uri = "hangar://server_a/demo://missing"
        low = _register()
        with (
            patch("mcp_hangar.fastmcp_server.prompt_proxy._upstream_ids", return_value=["server_a"]),
            patch.object(
                rt,
                "_relay_read",
                return_value={
                    "error": {
                        "code": -32602,
                        "message": "Resource not found",
                        "data": {"uri": "demo://missing"},
                    }
                },
            ),
            pytest.raises(McpError) as excinfo,
        ):
            await _call_read(low, uri)

        assert excinfo.value.error.code == INVALID_PARAMS == -32602
        assert excinfo.value.error.data == {"uri": uri}
