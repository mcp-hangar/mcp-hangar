"""McpServer group endpoint handlers for the REST API.

Implements GET/POST endpoints for mcp_server group operations,
reading directly from the application context groups dict.
"""

from typing import Any

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
from ...application.group_events import publish_group_events
from ...domain.exceptions import McpServerNotFoundError
from ...domain.model.mcp_server_group import McpServerGroup
from ..context import get_context
from .middleware import dispatch_command
from .request_body import json_body, missing_fields
from .serializers import HangarJSONResponse
from .tenant_scope import caller_tenant


def _status(group: McpServerGroup, tenant_id: str | None) -> dict[str, Any]:
    """The group as *tenant_id* is shown it, as ``hangar_details`` shows it (#2155); no tenant, the whole group."""
    return group.to_status_dict() if tenant_id is None else group.status_for_tenant(tenant_id)


async def list_groups(request: Request) -> HangarJSONResponse:
    """List all mcp_server groups with summary info.

    Returns:
        JSON with {"groups": [...]} array of group summaries. For a caller
        whose principal has a tenant, each group lists only the members that
        tenant's calls may be routed to, with every count, state and
        is_available taken over them and no min_healthy or circuit_open.
    """
    ctx = get_context()
    tenant_id = caller_tenant(request)
    return HangarJSONResponse({"groups": [_status(g, tenant_id) for g in ctx.groups.values()]})


async def get_group(request: Request) -> HangarJSONResponse:
    """Get detailed info for a single mcp_server group.

    Path params:
        group_id: Group identifier.

    Returns:
        JSON with group detail including members and circuit breaker state.
        For a caller whose principal has a tenant, the view ``list_groups``
        gives: a group the tenant has no routable member of is an empty member
        list, not a 404.

    Raises:
        McpServerNotFoundError: If group_id is not found.
    """
    group_id = request.path_params["group_id"]
    ctx = get_context()
    group = ctx.groups.get(group_id)
    if group is None:
        raise McpServerNotFoundError(mcp_server_id=group_id)
    return HangarJSONResponse(_status(group, caller_tenant(request)))


async def rebalance_group(request: Request) -> HangarJSONResponse:
    """Trigger a rebalance on a mcp_server group.

    Path params:
        group_id: Group identifier.

    Returns:
        JSON with {"status": "rebalanced", "group_id": ...}.

    Raises:
        McpServerNotFoundError: If group_id is not found.
    """
    group_id = request.path_params["group_id"]
    ctx = get_context()
    group = ctx.groups.get(group_id)
    if group is None:
        raise McpServerNotFoundError(mcp_server_id=group_id)
    await run_in_threadpool(group.rebalance)
    # On this request, as the tool surface does (#1410). Both rebalance the
    # same aggregate, so one of them draining and the other not would make
    # whether an operator hears about it depend on which surface they used.
    await run_in_threadpool(publish_group_events, ctx.event_bus, group)
    return HangarJSONResponse({"status": "rebalanced", "group_id": group_id})


async def create_group(request: Request) -> HangarJSONResponse:
    """Create a new mcp_server group.

    Body:
        group_id: Unique identifier for the group.
        strategy: Load balancing strategy (e.g. round_robin).
        min_healthy: Optional minimum healthy members.
        description: Optional human-readable description.

    Returns:
        JSON with {"group_id": ..., "created": true} and HTTP 201.
    """
    body = await json_body(request)
    if (invalid := missing_fields(body, "group_id")) is not None:
        return invalid
    result = await dispatch_command(
        CreateGroupCommand(
            group_id=body["group_id"],
            strategy=body.get("strategy", "round_robin"),
            min_healthy=body.get("min_healthy", 1),
            description=body.get("description"),
        )
    )
    return HangarJSONResponse(result, status_code=201)


async def update_group(request: Request) -> HangarJSONResponse:
    """Update an existing mcp_server group.

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
    body = await json_body(request)
    if (invalid := missing_fields(body)) is not None:
        return invalid
    result = await dispatch_command(
        UpdateGroupCommand(
            group_id=group_id,
            strategy=body.get("strategy"),
            min_healthy=body.get("min_healthy"),
            description=body.get("description"),
        )
    )
    return HangarJSONResponse(result)


async def delete_group(request: Request) -> HangarJSONResponse:
    """Delete a mcp_server group.

    Path params:
        group_id: Group identifier.

    Returns:
        JSON with {"group_id": ..., "deleted": true} and HTTP 200.
    """
    group_id = request.path_params["group_id"]
    result = await dispatch_command(DeleteGroupCommand(group_id=group_id))
    return HangarJSONResponse(result)


async def add_group_member(request: Request) -> HangarJSONResponse:
    """Add a mcp_server as a member of a group.

    Path params:
        group_id: Group identifier.

    Body:
        member_id: McpServer ID to add (mapped to mcp_server_id in command).
        weight: Optional routing weight.
        priority: Optional routing priority.

    Returns:
        JSON with {"group_id": ..., "mcp_server_id": ..., "added": true} and HTTP 201.
    """
    group_id = request.path_params["group_id"]
    body = await json_body(request)
    if (invalid := missing_fields(body, "member_id")) is not None:
        return invalid
    result = await dispatch_command(
        AddGroupMemberCommand(
            group_id=group_id,
            mcp_server_id=body["member_id"],
            weight=body.get("weight", 1),
            priority=body.get("priority", 1),
        )
    )
    return HangarJSONResponse(result, status_code=201)


async def remove_group_member(request: Request) -> HangarJSONResponse:
    """Remove a mcp_server from a group.

    Path params:
        group_id: Group identifier.
        member_id: McpServer ID to remove.

    Returns:
        JSON with {"group_id": ..., "mcp_server_id": ..., "removed": true} and HTTP 200.
    """
    group_id = request.path_params["group_id"]
    member_id = request.path_params["member_id"]
    result = await dispatch_command(RemoveGroupMemberCommand(group_id=group_id, mcp_server_id=member_id))
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
