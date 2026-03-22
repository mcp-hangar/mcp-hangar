"""Provider group endpoint handlers for the REST API.

Implements GET/POST endpoints for provider group operations,
reading directly from the application context groups dict.
"""

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.routing import Route

from ...application.commands.crud_commands import (
    AddGroupMemberCommand,
    CreateGroupCommand,
    DeleteGroupCommand,
    RemoveGroupMemberCommand,
    UpdateGroupCommand,
)
from ...domain.exceptions import ProviderNotFoundError
from ..context import get_context
from .middleware import dispatch_command
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


async def create_group(request: Request) -> HangarJSONResponse:
    """Create a new provider group.

    Body:
        group_id: Unique identifier for the group.
        strategy: Load balancing strategy (e.g. round_robin).
        min_healthy: Optional minimum healthy members.
        description: Optional human-readable description.

    Returns:
        JSON with {"group_id": ..., "created": true} and HTTP 201.
    """
    body = await request.json()
    result = await dispatch_command(
        CreateGroupCommand(
            group_id=body["group_id"],
            strategy=body.get("strategy", "round_robin"),
            min_healthy=body.get("min_healthy"),
            description=body.get("description"),
        )
    )
    return HangarJSONResponse(result, status_code=201)


async def update_group(request: Request) -> HangarJSONResponse:
    """Update an existing provider group.

    Path params:
        group_id: Group identifier.

    Body:
        strategy: Optional new load balancing strategy.
        min_healthy: Optional new minimum healthy members.
        description: Optional new description.

    Returns:
        JSON with {"group_id": ..., "updated": true} and HTTP 200.
    """
    group_id = request.path_params["group_id"]
    body = await request.json()
    result = await dispatch_command(
        UpdateGroupCommand(
            group_id=group_id,
            min_healthy=body.get("min_healthy"),
            description=body.get("description"),
        )
    )
    return HangarJSONResponse(result)


async def delete_group(request: Request) -> HangarJSONResponse:
    """Delete a provider group.

    Path params:
        group_id: Group identifier.

    Returns:
        JSON with {"group_id": ..., "deleted": true} and HTTP 200.
    """
    group_id = request.path_params["group_id"]
    result = await dispatch_command(DeleteGroupCommand(group_id=group_id))
    return HangarJSONResponse(result)


async def add_group_member(request: Request) -> HangarJSONResponse:
    """Add a provider as a member of a group.

    Path params:
        group_id: Group identifier.

    Body:
        member_id: Provider ID to add (mapped to provider_id in command).
        weight: Optional routing weight.
        priority: Optional routing priority.

    Returns:
        JSON with {"group_id": ..., "provider_id": ..., "added": true} and HTTP 201.
    """
    group_id = request.path_params["group_id"]
    body = await request.json()
    result = await dispatch_command(
        AddGroupMemberCommand(
            group_id=group_id,
            provider_id=body["member_id"],
            weight=body.get("weight"),
            priority=body.get("priority"),
        )
    )
    return HangarJSONResponse(result, status_code=201)


async def remove_group_member(request: Request) -> HangarJSONResponse:
    """Remove a provider from a group.

    Path params:
        group_id: Group identifier.
        member_id: Provider ID to remove.

    Returns:
        JSON with {"group_id": ..., "provider_id": ..., "removed": true} and HTTP 200.
    """
    group_id = request.path_params["group_id"]
    member_id = request.path_params["member_id"]
    result = await dispatch_command(RemoveGroupMemberCommand(group_id=group_id, provider_id=member_id))
    return HangarJSONResponse(result)


# Route definitions for mounting in the API router
group_routes = [
    Route("/", list_groups, methods=["GET"]),
    Route("/", create_group, methods=["POST"]),
    Route("/{group_id:str}", get_group, methods=["GET"]),
    Route("/{group_id:str}", update_group, methods=["PUT"]),
    Route("/{group_id:str}", delete_group, methods=["DELETE"]),
    Route("/{group_id:str}/rebalance", rebalance_group, methods=["POST"]),
    Route("/{group_id:str}/members", add_group_member, methods=["POST"]),
    Route("/{group_id:str}/members/{member_id:str}", remove_group_member, methods=["DELETE"]),
]
