"""The SDK's pre-dispatch tools/list must not recount the governance-cost metric (#1049).

A 2026-07-28 tools/call with arguments resolves the tool schema by invoking
our tools/list handler on the same HTTP request. That listing is not a
surface the client received: PROJECTED_TOOLS / EMPTY_PROJECTION_TOTAL exist
to measure what was handed out, not traffic. The identity-scoped map is
built once and reused for the call.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anyio

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server import flat_tool_projection


def _principal() -> Principal:
    return Principal(id=PrincipalId("user:alice"), type=PrincipalType.USER, tenant_id="tenant:a")


def _ctx(principal: Principal, *, envelope: str) -> SimpleNamespace:
    """SDK v2 ServerRequestContext stand-in sharing one Request across list+call."""
    payload = (
        b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"add","arguments":{"a":1}}}'
        if envelope == "tools/call"
        else b'{"jsonrpc":"2.0","method":"tools/list","params":{}}'
    )
    request = SimpleNamespace(
        state=SimpleNamespace(auth=SimpleNamespace(principal=principal)),
        _body=payload,
    )
    return SimpleNamespace(request=request)


def _registered() -> dict[str, Any]:
    handlers: dict[str, Any] = {}

    class _Low:
        def add_request_handler(self, method, params_type, handler):
            handlers[method] = handler

    flat_tool_projection.register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low()))
    return handlers


def _count(kind: str) -> float:
    _, _, counts = prometheus_metrics.PROJECTED_TOOLS.collect()
    return next((s.value for s in counts if s.labels.get("kind") == kind), 0.0)


def _empty_total() -> float:
    return sum(s.value for s in prometheus_metrics.EMPTY_PROJECTION_TOTAL.collect())


def _patch_builders(monkeypatch, builds: list[str | None] | None = None) -> None:
    monkeypatch.setattr(
        flat_tool_projection,
        "_build_flat_map",
        lambda tenant_id: ((builds.append(tenant_id) if builds is not None else None), {})[1],
    )
    monkeypatch.setattr(flat_tool_projection, "_build_mcp_tool_list", lambda _map: [])
    monkeypatch.setattr(
        "mcp_hangar.server.tools.tool_permissions.management_tools_for",
        lambda _ctx: set(),
    )


class TestAToolsCallDoesNotRecountTheProjection:
    def test_list_then_call_on_the_same_request_build_the_map_once(self, monkeypatch) -> None:
        builds: list[str | None] = []
        _patch_builders(monkeypatch, builds)

        handlers = _registered()
        ctx = _ctx(_principal(), envelope="tools/call")
        params = SimpleNamespace(name="add", arguments={"a": 1})

        async def _drive() -> None:
            await handlers["tools/list"](ctx, params)
            try:
                await handlers["tools/call"](ctx, params)
            except Exception:  # noqa: BLE001 -- empty map is METHOD_NOT_FOUND; identity+memo are under test
                pass

        anyio.run(_drive)

        assert builds == ["tenant:a"], (
            f"projection rebuilt {builds!r}; pre-dispatch list and call must share one map for one principal"
        )

    def test_a_client_visible_list_is_counted_a_nested_call_list_is_not(self, monkeypatch) -> None:
        _patch_builders(monkeypatch)

        handlers = _registered()
        principal = _principal()
        before_governed = _count("governed")
        before_empty = _empty_total()

        async def _drive() -> None:
            await handlers["tools/list"](_ctx(principal, envelope="tools/list"), SimpleNamespace())
            await handlers["tools/list"](_ctx(principal, envelope="tools/call"), SimpleNamespace())

        anyio.run(_drive)

        assert _count("governed") == before_governed + 1
        assert _empty_total() == before_empty + 1


class TestThePreDispatchListingSeesTheCallersPrincipal:
    """The nested listing must be scoped to the caller it is validating (#874 class).

    If `bind_caller_identity` were skipped on the pre-dispatch path, the
    listing would resolve to no tenant, project nothing, and the SDK would
    quietly skip Mcp-Param validation for a call that still runs (#1053).
    Nothing asserted the principal until this test.
    """

    def test_the_nested_listing_resolves_the_same_principal_as_the_call(self, monkeypatch) -> None:
        seen: dict[str, str | None] = {}

        def _build(tenant_id: str | None) -> dict[str, tuple[str, str]]:
            seen["list"] = tenant_id
            return {}

        monkeypatch.setattr(flat_tool_projection, "_build_flat_map", _build)
        monkeypatch.setattr(flat_tool_projection, "_build_mcp_tool_list", lambda _map: [])
        monkeypatch.setattr(
            "mcp_hangar.server.tools.tool_permissions.management_tools_for",
            lambda _ctx: set(),
        )

        memoised = flat_tool_projection._flat_map_for_request

        def _spy(mcp_ctx: Any, tenant_id: str | None) -> dict[str, tuple[str, str]]:
            seen["call"] = tenant_id
            return memoised(mcp_ctx, tenant_id)

        monkeypatch.setattr(flat_tool_projection, "_flat_map_for_request", _spy)

        handlers = _registered()
        ctx = _ctx(_principal(), envelope="tools/call")
        params = SimpleNamespace(name="add", arguments={"a": 1})

        async def _drive() -> None:
            # The SDK resolves the schema by invoking our own tools/list on the
            # same HTTP request, then dispatches the call.
            await handlers["tools/list"](ctx, params)
            try:
                await handlers["tools/call"](ctx, params)
            except Exception:  # noqa: BLE001 -- empty map is METHOD_NOT_FOUND; identity is under test
                pass

        anyio.run(_drive)

        assert seen.get("list") is not None, (
            "the pre-dispatch tools/list resolved to an anonymous projection; "
            "Mcp-Param validation would be silently skipped for a call that still runs (#1053)"
        )
        assert seen["list"] == "tenant:a"
        assert seen["call"] == seen["list"], (
            f"nested listing saw {seen['list']!r}, the call saw {seen['call']!r}; "
            "a schema validated for one principal cannot gate another's call"
        )
