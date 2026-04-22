"""REST API endpoint for listing all tools across all mcp_servers.

Used by hangar-agent supervisor to sync tool inventory to hangar-cloud.
"""

from starlette.requests import Request
from starlette.routing import Route

from ...application.queries import (
    GetMcpServerToolsQuery,
    ListMcpServersQuery,
)
from ...logging_config import get_logger
from .middleware import dispatch_query
from .serializers import HangarJSONResponse

logger = get_logger(__name__)


async def list_all_tools(request: Request) -> HangarJSONResponse:
    """List all tools across all mcp_servers.

    Returns:
        JSON with {"tools": [...]} where each tool includes mcp_server_id,
        tool_name, description, and input_schema.
    """
    mcp_servers = await dispatch_query(ListMcpServersQuery(state_filter=None))
    all_tools = []
    for p in mcp_servers:
        try:
            tools = await dispatch_query(GetMcpServerToolsQuery(mcp_server_id=p.mcp_server_id))
            for t in tools:
                td = t.to_dict()
                all_tools.append(
                    {
                        "mcp_server_id": p.mcp_server_id,
                        "tool_name": td.get("name", ""),
                        "description": td.get("description", ""),
                        "input_schema": str(td.get("inputSchema", "")),
                    }
                )
        except (RuntimeError, OSError, ValueError, TimeoutError):
            logger.debug("tools_fetch_skipped", mcp_server_id=p.mcp_server_id)
    return HangarJSONResponse({"tools": all_tools})


tools_routes = [
    Route("/", list_all_tools, methods=["GET"]),
]
