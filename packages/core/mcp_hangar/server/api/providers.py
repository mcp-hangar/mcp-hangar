"""Provider endpoint handlers for the REST API.

Implements GET/POST endpoints for provider CRUD operations,
routing through the CQRS dispatch helpers.
"""

from starlette.requests import Request
from starlette.routing import Route

from ...application.commands.commands import StartProviderCommand, StopProviderCommand
from ...application.queries.queries import (
    GetProviderHealthQuery,
    GetProviderQuery,
    GetProviderToolsQuery,
    GetToolInvocationHistoryQuery,
    ListProvidersQuery,
)
from .middleware import dispatch_command, dispatch_query
from .serializers import HangarJSONResponse


async def list_providers(request: Request) -> HangarJSONResponse:
    """List all providers, optionally filtered by state.

    Query params:
        state: Optional state filter (cold, ready, degraded, dead)

    Returns:
        JSON with {"providers": [...]} array of provider summaries.
    """
    state = request.query_params.get("state")
    result = await dispatch_query(ListProvidersQuery(state_filter=state))
    return HangarJSONResponse({"providers": [p.to_dict() for p in result]})


async def get_provider(request: Request) -> HangarJSONResponse:
    """Get details for a single provider.

    Path params:
        provider_id: Provider identifier.

    Returns:
        JSON with provider details including tools and health.
    """
    provider_id = request.path_params["provider_id"]
    result = await dispatch_query(GetProviderQuery(provider_id=provider_id))
    return HangarJSONResponse(result.to_dict())


async def start_provider(request: Request) -> HangarJSONResponse:
    """Start a provider.

    Path params:
        provider_id: Provider identifier.

    Returns:
        JSON with start result.
    """
    provider_id = request.path_params["provider_id"]
    result = await dispatch_command(StartProviderCommand(provider_id=provider_id))
    return HangarJSONResponse(result)


async def stop_provider(request: Request) -> HangarJSONResponse:
    """Stop a provider.

    Path params:
        provider_id: Provider identifier.

    Request body (optional JSON):
        reason: Reason for stopping (default: "user_request").

    Returns:
        JSON with stop result.
    """
    provider_id = request.path_params["provider_id"]
    reason = "user_request"
    try:
        body = await request.json()
        reason = body.get("reason", reason)
    except Exception:  # empty body or invalid JSON
        pass

    result = await dispatch_command(StopProviderCommand(provider_id=provider_id, reason=reason))
    return HangarJSONResponse(result)


async def get_provider_tools(request: Request) -> HangarJSONResponse:
    """Get tool list for a provider.

    Path params:
        provider_id: Provider identifier.

    Returns:
        JSON with {"tools": [...]} array of tool info.
    """
    provider_id = request.path_params["provider_id"]
    result = await dispatch_query(GetProviderToolsQuery(provider_id=provider_id))
    return HangarJSONResponse({"tools": [t.to_dict() for t in result]})


async def get_provider_health(request: Request) -> HangarJSONResponse:
    """Get health info for a provider.

    Path params:
        provider_id: Provider identifier.

    Returns:
        JSON with health status information.
    """
    provider_id = request.path_params["provider_id"]
    result = await dispatch_query(GetProviderHealthQuery(provider_id=provider_id))
    return HangarJSONResponse(result.to_dict())


async def get_provider_tool_history(request: Request) -> HangarJSONResponse:
    """Get tool invocation history for a provider.

    Path params:
        provider_id: Provider identifier.
    Query params:
        limit: Max records to return (default 100, max 500).
        from_position: Event store version to start from (default 0).

    Returns:
        JSON with {"provider_id": ..., "history": [...], "total": int}.
    """
    provider_id = request.path_params["provider_id"]
    try:
        limit = int(request.query_params.get("limit", 100))
    except ValueError:
        limit = 100
    try:
        from_position = int(request.query_params.get("from_position", 0))
    except ValueError:
        from_position = 0
    result = await dispatch_query(
        GetToolInvocationHistoryQuery(
            provider_id=provider_id,
            limit=limit,
            from_position=from_position,
        )
    )
    return HangarJSONResponse(result)


# Route definitions for mounting in the API router
provider_routes = [
    Route("/", list_providers, methods=["GET"]),
    Route("/{provider_id:str}", get_provider, methods=["GET"]),
    Route("/{provider_id:str}/start", start_provider, methods=["POST"]),
    Route("/{provider_id:str}/stop", stop_provider, methods=["POST"]),
    Route("/{provider_id:str}/tools", get_provider_tools, methods=["GET"]),
    Route("/{provider_id:str}/health", get_provider_health, methods=["GET"]),
    Route("/{provider_id:str}/tools/history", get_provider_tool_history, methods=["GET"]),
]
