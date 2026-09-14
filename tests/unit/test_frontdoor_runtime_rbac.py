from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_hangar.fastmcp_server import flat_tool_projection as projection
from mcp_hangar.server.tools import batch, tool_permissions


@pytest.fixture
def authorization(monkeypatch):
    principal = SimpleNamespace(is_anonymous=lambda: False)
    authz = MagicMock()
    monkeypatch.setattr(
        batch,
        "get_context",
        lambda: SimpleNamespace(auth_components=SimpleNamespace(enabled=True, authz_middleware=authz)),
    )
    context = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(state=SimpleNamespace(auth=SimpleNamespace(principal=principal)))
        )
    )
    return authz, context


def test_projection_filters_each_upstream_permission_and_fails_closed(authorization):
    authz, context = authorization
    authz.authorize.side_effect = [None, PermissionError()]
    assert projection._authorized_flat_map(
        context, {"safe": ("provider", "read"), "unsafe": ("provider", "write")}
    ) == {"safe": ("provider", "read")}
    assert authz.authorize.call_args.kwargs["resource_id"] == "write"
    authz.authorize.side_effect = RuntimeError("authorization unavailable")
    assert projection._authorized_flat_map(context, {"safe": ("provider", "read")}) == {}
    assert projection._authorized_flat_map(None, {"safe": ("provider", "read")}) == {}


@pytest.mark.asyncio
async def test_flat_dispatch_rechecks_rbac_even_for_previously_listed_tool(monkeypatch, authorization):
    authz, context = authorization
    authz.authorize.side_effect = PermissionError()
    handlers = {}
    mcp = MagicMock()
    mcp._mcp_server.list_tools = lambda: lambda fn: handlers.setdefault("list", fn)
    mcp._mcp_server.call_tool = lambda **kwargs: lambda fn: handlers.setdefault("call", fn)
    projection.register_flat_tool_handlers(mcp)
    monkeypatch.setattr(projection, "_settled_flat_map", AsyncMock(return_value={"read": ("provider", "read")}))
    monkeypatch.setattr(tool_permissions, "management_tools_for", lambda _: frozenset())
    executor = MagicMock()
    monkeypatch.setattr(batch, "BatchExecutor", executor)
    with pytest.raises(Exception, match="not found"):
        await handlers["call"]("read", {}, context)
    executor.assert_not_called()
