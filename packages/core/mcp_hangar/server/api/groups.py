"""Provider group endpoint handlers for the REST API.

Implements GET/POST endpoints for provider group operations,
reading directly from the application context groups dict.
"""

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.routing import Route

from ...domain.exceptions import ProviderNotFoundError
from ..context import get_context
from .serializers import HangarJSONResponse


async def list_groups(request: Request) -> HangarJSONResponse:
    """List all provider groups with summary info.

    Returns:
        JSON with {"groups": [...]} array of group summaries.
    """
    ctx = get_context()
    groups = ctx.groups
    return HangarJSONResponse({"groups": [g.to_status_dict() for g in groups.values()]})


async def get_group(request: Request) -> HangarJSONResponse:
    """Get detailed info for a single provider group.

    Path params:
        group_id: Group identifier.

    Returns:
        JSON with group detail including members and circuit breaker state.

    Raises:
        ProviderNotFoundError: If group_id is not found.
    """
    group_id = request.path_params["group_id"]
    ctx = get_context()
    group = ctx.groups.get(group_id)
    if group is None:
        raise ProviderNotFoundError(provider_id=group_id)
    return HangarJSONResponse(group.to_status_dict())


async def rebalance_group(request: Request) -> HangarJSONResponse:
    """Trigger a rebalance on a provider group.

    Path params:
        group_id: Group identifier.

    Returns:
        JSON with {"status": "rebalanced", "group_id": ...}.

    Raises:
        ProviderNotFoundError: If group_id is not found.
    """
    group_id = request.path_params["group_id"]
    ctx = get_context()
    group = ctx.groups.get(group_id)
    if group is None:
        raise ProviderNotFoundError(provider_id=group_id)
    await run_in_threadpool(group.rebalance)
    return HangarJSONResponse({"status": "rebalanced", "group_id": group_id})


# Route definitions for mounting in the API router
group_routes = [
    Route("/", list_groups, methods=["GET"]),
    Route("/{group_id:str}", get_group, methods=["GET"]),
    Route("/{group_id:str}/rebalance", rebalance_group, methods=["POST"]),
]
