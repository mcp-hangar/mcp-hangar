"""Admin endpoints for runtime withdrawal/restore of a tool, prompt or resource (#235, #1141).

Provides:
    POST /admin/tools/{server}/{name}/withdraw   — runtime withdraw (survives reload)
    POST /admin/tools/{server}/{name}/restore    — remove runtime withdrawal

``name`` is a tool name, a prompt name, or -- for ``kind: "resource"`` -- the
resource's UPSTREAM uri (``demo://doc/1``, ``file:///data/x.txt``), the same
form ``withdrawn_resources:`` reads and ``is_governed_allowed`` matches on,
NOT the projected ``hangar://<upstream>/<uri>``. A uri carries slashes, so the
name segment is a ``path`` converter, anchored by the trailing verb.

Auth: requires the admin role (``mcp_servers`` resource, ``lifecycle`` action)
via the existing ``_check_permission`` pattern from ``mcp_servers.py``.
"""

import json

from starlette.requests import Request
from starlette.routing import Route

from ...application.read_models.tool_projection import get_tool_projection_registry
from ...domain.events import ToolRestored, ToolWithdrawn
from ..context import get_context
from .mcp_servers import _check_permission
from .serializers import HangarJSONResponse

_KINDS = ("tool", "prompt", "resource")


async def _parse_body(request: Request) -> tuple[str | None, str] | HangarJSONResponse:
    """``(tenant_id, kind)`` from the optional JSON body, or a 400.

    An absent ``kind`` is a tool -- the only thing this endpoint could withdraw
    before #1141, so a caller who never sent one is unchanged. Anything else
    that is not one of the three kinds is refused outright: falling back to
    ``"tool"`` would write the same-named tool's overlay, which is the
    collateral #1137 describes.
    """
    body: dict = {}
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            body = parsed
    except (json.JSONDecodeError, ValueError):
        pass
    kind = body.get("kind", "tool")
    if kind not in _KINDS:
        return HangarJSONResponse(
            {"error": "invalid_kind", "detail": f"kind must be one of {', '.join(_KINDS)}; got {kind!r}"},
            status_code=400,
        )
    return body.get("tenant_id") or None, kind


async def withdraw_tool(request: Request) -> HangarJSONResponse:
    """Withdraw a tool, prompt or resource at runtime for a tenant (or globally).

    Path params:
        server: MCP server identifier.
        tool: Tool name, prompt name, or upstream resource uri (see module docstring).

    Request body (optional JSON):
        tenant_id: Tenant to withdraw for. Omit (or ``null``) to withdraw
            globally for ALL tenants.
        kind: ``"tool"`` (default), ``"prompt"`` or ``"resource"``. Anything
            else is a 400 and nothing is written.

    Returns:
        JSON with {"withdrawn": true, "mcp_server": ..., "tool": ..., "kind": ..., "tenant_id": ...}.
    """
    _check_permission(request, resource_type="mcp_servers", action="lifecycle")

    server = request.path_params["server"]
    tool = request.path_params["tool"]
    parsed = await _parse_body(request)
    if isinstance(parsed, HangarJSONResponse):
        return parsed
    tenant_id, kind = parsed

    get_tool_projection_registry().withdraw(server, tool, tenant_id=tenant_id, kind=kind)

    ctx = get_context()
    ctx.event_bus.publish(ToolWithdrawn(tenant_id=tenant_id, mcp_server=server, tool=tool, kind=kind))

    return HangarJSONResponse(
        {"withdrawn": True, "mcp_server": server, "tool": tool, "kind": kind, "tenant_id": tenant_id}
    )


async def restore_tool(request: Request) -> HangarJSONResponse:
    """Restore a runtime-withdrawn tool, prompt or resource for a tenant (or remove the global entry).

    Affects ONLY the runtime overlay; a config-declared withdrawal independently
    persists (effective = config OR runtime).

    Path params:
        server: MCP server identifier.
        tool: Tool name, prompt name, or upstream resource uri (see module docstring).

    Request body (optional JSON):
        tenant_id: Tenant to restore. Omit (or ``null``) to remove the entire
            runtime entry (all-tenants restore).
        kind: ``"tool"`` (default), ``"prompt"`` or ``"resource"``. Anything
            else is a 400 and nothing is written.

    Returns:
        JSON with {"restored": true, "mcp_server": ..., "tool": ..., "kind": ..., "tenant_id": ...}.
    """
    _check_permission(request, resource_type="mcp_servers", action="lifecycle")

    server = request.path_params["server"]
    tool = request.path_params["tool"]
    parsed = await _parse_body(request)
    if isinstance(parsed, HangarJSONResponse):
        return parsed
    tenant_id, kind = parsed

    get_tool_projection_registry().restore(server, tool, tenant_id=tenant_id, kind=kind)

    ctx = get_context()
    ctx.event_bus.publish(ToolRestored(tenant_id=tenant_id, mcp_server=server, tool=tool, kind=kind))

    return HangarJSONResponse(
        {"restored": True, "mcp_server": server, "tool": tool, "kind": kind, "tenant_id": tenant_id}
    )


# Route definitions for mounting in the API router. `{tool:path}` so an
# upstream resource uri (`demo://doc/1`) can ride the segment; the trailing
# verb anchors it. `route_permissions.py` carries the same two templates.
admin_tools_routes = [
    Route("/{server:str}/{tool:path}/withdraw", withdraw_tool, methods=["POST"]),
    Route("/{server:str}/{tool:path}/restore", restore_tool, methods=["POST"]),
]
