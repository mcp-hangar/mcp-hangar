"""McpServer endpoint handlers for the REST API.

Implements GET/POST endpoints for mcp_server CRUD operations,
routing through the CQRS dispatch helpers.
"""

# pyright: reportAny=false, reportUnknownMemberType=false

import json

from starlette.requests import Request
from starlette.routing import Route

from ...domain.exceptions import MissingCredentialsError
from ...application.commands.commands import StartMcpServerCommand, StopMcpServerCommand
from ...application.commands.crud_commands import (
    CreateMcpServerCommand,
    DeleteMcpServerCommand,
    UpdateMcpServerCommand,
)
from ...application.queries.queries import (
    GetMcpServerHealthQuery,
    GetMcpServerQuery,
    GetMcpServerToolsQuery,
    GetToolInvocationHistoryQuery,
    ListMcpServersQuery,
)
from ...infrastructure.persistence.log_buffer import get_log_buffer
from ..context import get_context
from .middleware import dispatch_command, dispatch_query
from .serializers import HangarJSONResponse


def _check_permission(request: Request, resource_type: str, action: str) -> None:
    context = get_context()
    auth_components = getattr(context, "auth_components", None)
    authz_middleware = getattr(auth_components, "authz_middleware", None)

    if authz_middleware is None:
        return

    auth_context = getattr(request.state, "auth", None)
    principal = getattr(auth_context, "principal", None)

    if principal is None or principal.is_anonymous():
        raise MissingCredentialsError("Authentication required")

    authz_middleware.authorize(
        principal=principal,
        action=action,
        resource_type=resource_type,
        resource_id="*",
    )


async def list_mcp_servers(request: Request) -> HangarJSONResponse:
    """List all mcp_servers, optionally filtered by state.

    Query params:
        state: Optional state filter (cold, ready, degraded, dead)

    Returns:
        JSON with {"mcp_servers": [...]} array of mcp_server summaries.
    """
    _check_permission(request, resource_type="mcp_servers", action="read")
    state = request.query_params.get("state")
    result = await dispatch_query(ListMcpServersQuery(state_filter=state))
    return HangarJSONResponse({"mcp_servers": [p.to_dict() for p in result]})


async def get_mcp_server(request: Request) -> HangarJSONResponse:
    """Get details for a single mcp_server.

    Path params:
        mcp_server_id: McpServer identifier.

    Returns:
        JSON with mcp_server details including tools and health.
    """
    _check_permission(request, resource_type="mcp_servers", action="read")
    mcp_server_id = request.path_params["mcp_server_id"]
    result = await dispatch_query(GetMcpServerQuery(mcp_server_id=mcp_server_id))
    return HangarJSONResponse(result.to_dict())


async def start_mcp_server(request: Request) -> HangarJSONResponse:
    """Start a mcp_server.

    Path params:
        mcp_server_id: McpServer identifier.

    Returns:
        JSON with start result.
    """
    _check_permission(request, resource_type="mcp_servers", action="lifecycle")
    mcp_server_id = request.path_params["mcp_server_id"]
    result = await dispatch_command(StartMcpServerCommand(mcp_server_id=mcp_server_id))
    return HangarJSONResponse(result)


async def stop_mcp_server(request: Request) -> HangarJSONResponse:
    """Stop a mcp_server.

    Path params:
        mcp_server_id: McpServer identifier.

    Request body (optional JSON):
        reason: Reason for stopping (default: "user_request").

    Returns:
        JSON with stop result.
    """
    _check_permission(request, resource_type="mcp_servers", action="lifecycle")
    mcp_server_id = request.path_params["mcp_server_id"]
    reason = "user_request"
    try:
        body = await request.json()
        reason = body.get("reason", reason)
    except (json.JSONDecodeError, ValueError):  # empty body or invalid JSON
        pass

    result = await dispatch_command(StopMcpServerCommand(mcp_server_id=mcp_server_id, reason=reason))
    return HangarJSONResponse(result)


async def block_mcp_server(request: Request) -> HangarJSONResponse:
    """Block a mcp_server permanently for detection enforcement.

    Path params:
        mcp_server_id: McpServer identifier.

    Returns:
        JSON with mcp_server block result.
    """
    _check_permission(request, resource_type="mcp_servers", action="lifecycle")
    mcp_server_id = request.path_params["mcp_server_id"]
    await dispatch_command(StopMcpServerCommand(mcp_server_id=mcp_server_id, reason="detection_enforcement:block"))
    return HangarJSONResponse({"mcp_server_id": mcp_server_id, "blocked": True})


async def get_mcp_server_tools(request: Request) -> HangarJSONResponse:
    """Get tool list for a mcp_server.

    Path params:
        mcp_server_id: McpServer identifier.

    Returns:
        JSON with {"tools": [...]} array of tool info.
    """
    _check_permission(request, resource_type="mcp_servers", action="read")
    mcp_server_id = request.path_params["mcp_server_id"]
    result = await dispatch_query(GetMcpServerToolsQuery(mcp_server_id=mcp_server_id))
    return HangarJSONResponse({"tools": [t.to_dict() for t in result]})


async def get_mcp_server_health(request: Request) -> HangarJSONResponse:
    """Get health info for a mcp_server.

    Path params:
        mcp_server_id: McpServer identifier.

    Returns:
        JSON with health status information.
    """
    _check_permission(request, resource_type="mcp_servers", action="read")
    mcp_server_id = request.path_params["mcp_server_id"]
    result = await dispatch_query(GetMcpServerHealthQuery(mcp_server_id=mcp_server_id))
    return HangarJSONResponse(result.to_dict())


async def get_mcp_server_logs(request: Request) -> HangarJSONResponse:
    """Get buffered log lines for a mcp_server.

    Path params:
        mcp_server_id: McpServer identifier.
    Query params:
        lines: Number of most recent lines to return (default 100, max 1000).

    Returns:
        JSON with {"logs": [...], "mcp_server_id": str, "count": int}.
        Returns an empty list if the mcp_server exists but has no log buffer yet.
        Returns 404 if the mcp_server is not registered.
    """
    _check_permission(request, resource_type="mcp_servers", action="read")
    mcp_server_id = request.path_params["mcp_server_id"]
    try:
        lines = int(request.query_params.get("lines", 100))
    except ValueError:
        lines = 100
    lines = min(max(1, lines), 1000)

    # Raises McpServerNotFoundError (-> 404) if unknown mcp_server
    await dispatch_query(GetMcpServerQuery(mcp_server_id=mcp_server_id))

    buffer = get_log_buffer(mcp_server_id)
    if buffer is None:
        log_dicts: list[dict[str, object]] = []
    else:
        log_dicts = [line.to_dict() for line in buffer.tail(lines)]

    return HangarJSONResponse({"logs": log_dicts, "mcp_server_id": mcp_server_id, "count": len(log_dicts)})


async def get_mcp_server_tool_history(request: Request) -> HangarJSONResponse:
    """Get tool invocation history for a mcp_server.

    Path params:
        mcp_server_id: McpServer identifier.
    Query params:
        limit: Max records to return (default 100, max 500).
        from_position: Event store version to start from (default 0).

    Returns:
        JSON with {"mcp_server_id": ..., "history": [...], "total": int}.
    """
    _check_permission(request, resource_type="mcp_servers", action="read")
    mcp_server_id = request.path_params["mcp_server_id"]
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
            mcp_server_id=mcp_server_id,
            limit=limit,
            from_position=from_position,
        )
    )
    return HangarJSONResponse(result)


async def create_mcp_server(request: Request) -> HangarJSONResponse:
    """Create a new mcp_server.

    Request body (JSON):
        mcp_server_id: Unique mcp_server identifier (required).
        mode: McpServer mode -- subprocess, docker, remote (required).
        command: Subprocess command list (for subprocess mode).
        image: Docker image name (for docker mode).
        endpoint: HTTP endpoint URL (for remote mode).
        env: Environment variables dict.
        idle_ttl_s: Idle TTL in seconds (default 300).
        health_check_interval_s: Health check interval in seconds (default 60).
        description: Human-readable description.
        volumes: Volume mounts list (for docker mode).
        network: Docker network name (default "none").
        read_only: Read-only filesystem flag (default True).

    Returns:
        JSON with {"mcp_server_id": ..., "created": true}, status 201.

    Raises:
        ValidationError: If mcp_server_id already exists (-> 422).
    """
    _check_permission(request, resource_type="mcp_servers", action="write")
    body = await request.json()
    try:
        result = await dispatch_command(
            CreateMcpServerCommand(
                mcp_server_id=body["mcp_server_id"],
                mode=body["mode"],
                command=body.get("command"),
                image=body.get("image"),
                endpoint=body.get("endpoint"),
                env=body.get("env", {}),
                idle_ttl_s=body.get("idle_ttl_s", 300),
                health_check_interval_s=body.get("health_check_interval_s", 60),
                description=body.get("description"),
                source="api",
            )
        )
    except ValueError as exc:
        if str(exc) == "SSRF blocked: endpoint resolves to private address":
            return HangarJSONResponse({"error": "ssrf_blocked"}, status_code=400)
        raise
    return HangarJSONResponse(result, status_code=201)


async def update_mcp_server(request: Request) -> HangarJSONResponse:
    """Update mutable configuration fields on an existing mcp_server.

    Path params:
        mcp_server_id: McpServer identifier.

    Request body (JSON, all fields optional):
        description: New human-readable description.
        env: New environment variables dict (replaces existing).
        idle_ttl_s: New idle TTL in seconds.
        health_check_interval_s: New health check interval in seconds.

    Returns:
        JSON with {"mcp_server_id": ..., "updated": true}, status 200.

    Raises:
        McpServerNotFoundError: If mcp_server does not exist (-> 404).
    """
    _check_permission(request, resource_type="mcp_servers", action="write")
    mcp_server_id = request.path_params["mcp_server_id"]
    body = await request.json()
    result = await dispatch_command(
        UpdateMcpServerCommand(
            mcp_server_id=mcp_server_id,
            description=body.get("description"),
            env=body.get("env"),
            idle_ttl_s=body.get("idle_ttl_s"),
            health_check_interval_s=body.get("health_check_interval_s"),
            source="api",
        )
    )
    return HangarJSONResponse(result)


async def delete_mcp_server(request: Request) -> HangarJSONResponse:
    """Delete a mcp_server, stopping it first if running.

    Path params:
        mcp_server_id: McpServer identifier.

    Returns:
        JSON with {"mcp_server_id": ..., "deleted": true}, status 200.

    Raises:
        McpServerNotFoundError: If mcp_server does not exist (-> 404).
    """
    _check_permission(request, resource_type="mcp_servers", action="lifecycle")
    mcp_server_id = request.path_params["mcp_server_id"]
    result = await dispatch_command(
        DeleteMcpServerCommand(
            mcp_server_id=mcp_server_id,
            source="api",
        )
    )
    return HangarJSONResponse(result)


# Route definitions for mounting in the API router
mcp_server_routes = [
    Route("/", list_mcp_servers, methods=["GET"]),
    Route("/", create_mcp_server, methods=["POST"]),
    Route("/{mcp_server_id:str}", get_mcp_server, methods=["GET"]),
    Route("/{mcp_server_id:str}", update_mcp_server, methods=["PUT", "PATCH"]),
    Route("/{mcp_server_id:str}", delete_mcp_server, methods=["DELETE"]),
    Route("/{mcp_server_id:str}/start", start_mcp_server, methods=["POST"]),
    Route("/{mcp_server_id:str}/stop", stop_mcp_server, methods=["POST"]),
    Route("/{mcp_server_id:str}/block", block_mcp_server, methods=["POST"]),
    Route("/{mcp_server_id:str}/tools", get_mcp_server_tools, methods=["GET"]),
    Route("/{mcp_server_id:str}/health", get_mcp_server_health, methods=["GET"]),
    Route("/{mcp_server_id:str}/logs", get_mcp_server_logs, methods=["GET"]),
    Route("/{mcp_server_id:str}/tools/history", get_mcp_server_tool_history, methods=["GET"]),
]
