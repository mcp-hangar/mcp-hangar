"""Admin endpoints for runtime tool withdrawal/restore (issue #235).

Provides:
    POST /admin/tools/{server}/{tool}/withdraw   — runtime withdraw (survives reload)
    POST /admin/tools/{server}/{tool}/restore    — remove runtime withdrawal

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


async def withdraw_tool(request: Request) -> HangarJSONResponse:
    """Withdraw a tool at runtime for a tenant (or globally).

    Path params:
        server: MCP server identifier.
        tool: Tool name.

    Request body (optional JSON):
        tenant_id: Tenant to withdraw for. Omit (or ``null``) to withdraw
            globally for ALL tenants.

    Returns:
        JSON with {"withdrawn": true, "mcp_server": ..., "tool": ..., "tenant_id": ...}.
    """
    _check_permission(request, resource_type="mcp_servers", action="lifecycle")

    server = request.path_params["server"]
    tool = request.path_params["tool"]
    tenant_id: str | None = None
    try:
        body = await request.json()
        tenant_id = body.get("tenant_id") or None
    except (json.JSONDecodeError, ValueError):
        pass

    get_tool_projection_registry().withdraw(server, tool, tenant_id=tenant_id)

    ctx = get_context()
    ctx.event_bus.publish(ToolWithdrawn(tenant_id=tenant_id, mcp_server=server, tool=tool))

    return HangarJSONResponse({"withdrawn": True, "mcp_server": server, "tool": tool, "tenant_id": tenant_id})


async def restore_tool(request: Request) -> HangarJSONResponse:
    """Restore a runtime-withdrawn tool for a tenant (or remove the global entry).

    Affects ONLY the runtime overlay; a config-declared withdrawal independently
    persists (effective = config OR runtime).

    Path params:
        server: MCP server identifier.
        tool: Tool name.

    Request body (optional JSON):
        tenant_id: Tenant to restore. Omit (or ``null``) to remove the entire
            runtime entry (all-tenants restore).

    Returns:
        JSON with {"restored": true, "mcp_server": ..., "tool": ..., "tenant_id": ...}.
    """
    _check_permission(request, resource_type="mcp_servers", action="lifecycle")

    server = request.path_params["server"]
    tool = request.path_params["tool"]
    tenant_id: str | None = None
    try:
        body = await request.json()
        tenant_id = body.get("tenant_id") or None
    except (json.JSONDecodeError, ValueError):
        pass

    get_tool_projection_registry().restore(server, tool, tenant_id=tenant_id)

    ctx = get_context()
    ctx.event_bus.publish(ToolRestored(tenant_id=tenant_id, mcp_server=server, tool=tool))

    return HangarJSONResponse({"restored": True, "mcp_server": server, "tool": tool, "tenant_id": tenant_id})


# Route definitions for mounting in the API router
admin_tools_routes = [
    Route("/{server:str}/{tool:str}/withdraw", withdraw_tool, methods=["POST"]),
    Route("/{server:str}/{tool:str}/restore", restore_tool, methods=["POST"]),
]
