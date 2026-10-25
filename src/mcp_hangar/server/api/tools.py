"""REST API endpoint for listing all tools across all mcp_servers.

Exposes GET /tools for querying the aggregated tool inventory.
"""

from starlette.requests import Request
from starlette.routing import Route

from ...application.queries import (
    GetMcpServerToolsQuery,
    ListMcpServersQuery,
)
from ...logging_config import get_logger
from .mcp_servers import tool_with_digest
from .middleware import dispatch_query
from .serializers import HangarJSONResponse

logger = get_logger(__name__)


async def list_all_tools(request: Request) -> HangarJSONResponse:
    """List all tools across all mcp_servers.

    Returns:
        JSON with {"tools": [...]} where each tool includes mcp_server_id,
        tool_name, description, input_schema and digest, and pinned_digest
        when the tool has an all-tenants pin (#1528). A tool that drifted from
        a pin in block mode, withheld from every listing a model reads, has
        ``withheld: "digest_drift"`` and no description or input_schema (#1776);
        a withdrawn one, or one the pins do not admit, has ``withheld: "withdrawn"``.
    """
    mcp_servers = await dispatch_query(ListMcpServersQuery(state_filter=None))
    all_tools = []
    for p in mcp_servers:
        try:
            tools = await dispatch_query(GetMcpServerToolsQuery(mcp_server_id=p.mcp_server_id))
            for t in tools:
                td = tool_with_digest(t.to_dict(), p.mcp_server_id)
                entry = {"mcp_server_id": p.mcp_server_id, "tool_name": td.get("name", "")}
                if "withheld" not in td:  # a withheld tool's description is not shown (#1776)
                    entry["description"] = td.get("description", "")
                    entry["input_schema"] = td.get("inputSchema") or {}
                entry.update({key: td[key] for key in ("digest", "pinned_digest", "withheld") if key in td})
                all_tools.append(entry)
        except (RuntimeError, OSError, ValueError, TimeoutError):
            logger.debug("tools_fetch_skipped", mcp_server_id=p.mcp_server_id)
    return HangarJSONResponse({"tools": all_tools})


tools_routes = [
    Route("/", list_all_tools, methods=["GET"]),
]
