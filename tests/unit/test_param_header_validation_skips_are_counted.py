"""Hangar counts Mcp-Param-* validation skips the SDK swallows (#1053).

The SDK fail-opens when it cannot produce a schema. Hangar sees the nested
tools/list (and handshake-era calls that still carry Mcp-Param-*), so the
metric is incremented here rather than by scraping SDK log lines.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar._sdk_compat import Tool as MCPTool
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server import flat_tool_projection
from mcp_hangar.metrics import get_metrics


def _principal() -> Principal:
    return Principal(id=PrincipalId("user:alice"), type=PrincipalType.USER, tenant_id="tenant:a")


def _ctx(
    principal: Principal,
    *,
    envelope: str,
    headers: dict[str, str] | None = None,
    arguments: dict[str, Any] | None = None,
) -> SimpleNamespace:
    args = {"a": 1} if arguments is None else arguments
    payload = json_call(args) if envelope == "tools/call" else b'{"jsonrpc":"2.0","method":"tools/list","params":{}}'
    request = SimpleNamespace(
        state=SimpleNamespace(auth=SimpleNamespace(principal=principal)),
        _body=payload,
        headers=headers or {},
    )
    return SimpleNamespace(request=request)


def json_call(arguments: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "add", "arguments": arguments},
        }
    ).encode()


def _registered() -> dict[str, Any]:
    handlers: dict[str, Any] = {}

    class _Low:
        def add_request_handler(self, method, params_type, handler):
            handlers[method] = handler

    flat_tool_projection.register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low()))
    return handlers


def _skip(reason: str) -> float:
    return next(
        (
            sample.value
            for sample in prometheus_metrics.PARAM_HEADER_VALIDATION_SKIPPED_TOTAL.collect()
            if sample.labels.get("reason") == reason
        ),
        0.0,
    )


def _patch_empty(monkeypatch) -> None:
    monkeypatch.setattr(flat_tool_projection, "_build_flat_map", lambda _tenant: {})
    monkeypatch.setattr(flat_tool_projection, "_build_mcp_tool_list", lambda _map: [])
    monkeypatch.setattr(
        "mcp_hangar.server.tools.tool_permissions.management_tools_for",
        lambda _ctx: set(),
    )


class TestParamHeaderValidationSkipsAreCounted:
    def test_the_skip_counter_is_on_the_exposition(self) -> None:
        out = get_metrics()
        assert "mcp_hangar_param_header_validation_skipped" in out
        assert "mcp_hangar_empty_projection" in out
        assert "mcp_hangar_projected_tools" in out

    def test_a_client_visible_list_is_not_a_skip(self, monkeypatch) -> None:
        _patch_empty(monkeypatch)
        handlers = _registered()
        before = _skip("tool_not_listed")

        async def _drive() -> None:
            await handlers["tools/list"](_ctx(_principal(), envelope="tools/list"), SimpleNamespace())

        anyio.run(_drive)
        assert _skip("tool_not_listed") == before

    def test_a_nested_call_list_that_omits_the_tool_is_tool_not_listed(self, monkeypatch) -> None:
        _patch_empty(monkeypatch)
        handlers = _registered()
        before = _skip("tool_not_listed")

        async def _drive() -> None:
            await handlers["tools/list"](_ctx(_principal(), envelope="tools/call"), SimpleNamespace())

        anyio.run(_drive)
        assert _skip("tool_not_listed") == before + 1

    def test_an_invalid_annotation_on_the_called_tool_is_a_skip(self, monkeypatch) -> None:
        tool = MCPTool.model_validate(
            {
                "name": "add",
                "description": "",
                "inputSchema": {
                    "type": "object",
                    "properties": {"a": {"type": "object", "x-mcp-header": "A"}},
                },
            }
        )
        monkeypatch.setattr(flat_tool_projection, "_build_flat_map", lambda _t: {"add": ("s", "add")})
        monkeypatch.setattr(flat_tool_projection, "_build_mcp_tool_list", lambda _m: [tool])
        monkeypatch.setattr(
            "mcp_hangar.server.tools.tool_permissions.management_tools_for",
            lambda _ctx: set(),
        )
        handlers = _registered()
        before_invalid = _skip("invalid_annotation")
        before_missing = _skip("tool_not_listed")

        async def _drive() -> None:
            await handlers["tools/list"](_ctx(_principal(), envelope="tools/call"), SimpleNamespace())

        anyio.run(_drive)
        assert _skip("invalid_annotation") == before_invalid + 1
        assert _skip("tool_not_listed") == before_missing

    def test_a_valid_annotation_is_not_a_skip(self, monkeypatch) -> None:
        tool = MCPTool.model_validate(
            {
                "name": "add",
                "description": "",
                "inputSchema": {
                    "type": "object",
                    "properties": {"a": {"type": "integer", "x-mcp-header": "A"}},
                },
            }
        )
        monkeypatch.setattr(flat_tool_projection, "_build_flat_map", lambda _t: {"add": ("s", "add")})
        monkeypatch.setattr(flat_tool_projection, "_build_mcp_tool_list", lambda _m: [tool])
        monkeypatch.setattr(
            "mcp_hangar.server.tools.tool_permissions.management_tools_for",
            lambda _ctx: set(),
        )
        handlers = _registered()
        before = sum(_skip(reason) for reason in ("tool_not_listed", "invalid_annotation", "listing_failed"))

        async def _drive() -> None:
            await handlers["tools/list"](_ctx(_principal(), envelope="tools/call"), SimpleNamespace())

        anyio.run(_drive)
        after = sum(_skip(reason) for reason in ("tool_not_listed", "invalid_annotation", "listing_failed"))
        assert after == before

    def test_a_listing_that_raises_during_a_call_is_listing_failed(self, monkeypatch) -> None:
        def _boom(_tenant: str | None) -> dict:
            raise RuntimeError("listing exploded")

        monkeypatch.setattr(flat_tool_projection, "_build_flat_map", _boom)
        monkeypatch.setattr(
            "mcp_hangar.server.tools.tool_permissions.management_tools_for",
            lambda _ctx: set(),
        )
        handlers = _registered()
        before = _skip("listing_failed")

        async def _drive() -> None:
            with pytest.raises(RuntimeError, match="listing exploded"):
                await handlers["tools/list"](_ctx(_principal(), envelope="tools/call"), SimpleNamespace())

        anyio.run(_drive)
        assert _skip("listing_failed") == before + 1

    def test_a_handshake_era_call_carrying_mcp_param_is_legacy_protocol(self, monkeypatch) -> None:
        _patch_empty(monkeypatch)
        handlers = _registered()
        before = _skip("legacy_protocol")
        ctx = _ctx(
            _principal(),
            envelope="tools/call",
            headers={"mcp-protocol-version": "2025-06-18", "mcp-param-region": "eu"},
        )

        async def _drive() -> None:
            with pytest.raises(Exception):  # noqa: BLE001 -- empty map is METHOD_NOT_FOUND
                await handlers["tools/call"](ctx, SimpleNamespace(name="add", arguments={"a": 1}))

        anyio.run(_drive)
        assert _skip("legacy_protocol") == before + 1
