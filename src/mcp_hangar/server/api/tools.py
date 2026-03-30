"""REST API endpoint for listing all tools across all providers.

Used by hangar-agent supervisor to sync tool inventory to hangar-cloud.
"""

from starlette.requests import Request
from starlette.routing import Route

from ...application.queries import (
    GetProviderToolsQuery,
    ListProvidersQuery,
)
from ...logging_config import get_logger
from .middleware import dispatch_query
from .serializers import HangarJSONResponse

logger = get_logger(__name__)


async def list_all_tools(request: Request) -> HangarJSONResponse:
    """List all tools across all providers.

    Returns:
        JSON with {"tools": [...]} where each tool includes provider_id,
        tool_name, description, and input_schema.
    """
    providers = await dispatch_query(ListProvidersQuery(state_filter=None))
    all_tools = []
    for p in providers:
        try:
            tools = await dispatch_query(
                GetProviderToolsQuery(provider_id=p.provider_id)
            )
            for t in tools:
                td = t.to_dict()
                all_tools.append({
                    "provider_id": p.provider_id,
                    "tool_name": td.get("name", ""),
                    "description": td.get("description", ""),
                    "input_schema": str(td.get("inputSchema", "")),
                })
        except Exception:
            logger.debug("tools_fetch_skipped", provider_id=p.provider_id)
    return HangarJSONResponse({"tools": all_tools})


tools_routes = [
    Route("/", list_all_tools, methods=["GET"]),
]


