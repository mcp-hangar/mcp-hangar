"""Provider endpoint handlers for the REST API.

Implements GET/POST endpoints for provider CRUD operations,
routing through the CQRS dispatch helpers.
"""

# pyright: reportAny=false, reportUnknownMemberType=false

import json

from starlette.requests import Request
from starlette.routing import Route

from ...application.commands.commands import StartProviderCommand, StopProviderCommand
from ...application.commands.crud_commands import (
    CreateProviderCommand,
    DeleteProviderCommand,
    UpdateProviderCommand,
)
from ...application.queries.queries import (
    GetProviderHealthQuery,
    GetProviderQuery,
    GetProviderToolsQuery,
    GetToolInvocationHistoryQuery,
    ListProvidersQuery,
)
from ...infrastructure.persistence.log_buffer import get_log_buffer
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
    except (json.JSONDecodeError, ValueError):  # empty body or invalid JSON
        pass

    result = await dispatch_command(StopProviderCommand(provider_id=provider_id, reason=reason))
    return HangarJSONResponse(result)


async def block_provider(request: Request) -> HangarJSONResponse:
    """Block a provider permanently for detection enforcement.

    Path params:
        provider_id: Provider identifier.

    Returns:
        JSON with provider block result.
    """
    provider_id = request.path_params["provider_id"]
    await dispatch_command(StopProviderCommand(provider_id=provider_id, reason="detection_enforcement:block"))
    return HangarJSONResponse({"provider_id": provider_id, "blocked": True})


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


async def get_provider_logs(request: Request) -> HangarJSONResponse:
    """Get buffered log lines for a provider.

    Path params:
        provider_id: Provider identifier.
    Query params:
        lines: Number of most recent lines to return (default 100, max 1000).

    Returns:
        JSON with {"logs": [...], "provider_id": str, "count": int}.
        Returns an empty list if the provider exists but has no log buffer yet.
        Returns 404 if the provider is not registered.
    """
    provider_id = request.path_params["provider_id"]
    try:
        lines = int(request.query_params.get("lines", 100))
    except ValueError:
        lines = 100
    lines = min(max(1, lines), 1000)

    # Raises ProviderNotFoundError (-> 404) if unknown provider
    await dispatch_query(GetProviderQuery(provider_id=provider_id))

    buffer = get_log_buffer(provider_id)
    if buffer is None:
        log_dicts: list[dict[str, object]] = []
    else:
        log_dicts = [line.to_dict() for line in buffer.tail(lines)]

    return HangarJSONResponse({"logs": log_dicts, "provider_id": provider_id, "count": len(log_dicts)})


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


async def create_provider(request: Request) -> HangarJSONResponse:
    """Create a new provider.

    Request body (JSON):
        provider_id: Unique provider identifier (required).
        mode: Provider mode -- subprocess, docker, remote (required).
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
        JSON with {"provider_id": ..., "created": true}, status 201.

    Raises:
        ValidationError: If provider_id already exists (-> 422).
    """
    body = await request.json()
    result = await dispatch_command(
        CreateProviderCommand(
            provider_id=body["provider_id"],
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
    return HangarJSONResponse(result, status_code=201)


async def update_provider(request: Request) -> HangarJSONResponse:
    """Update mutable configuration fields on an existing provider.

    Path params:
        provider_id: Provider identifier.

    Request body (JSON, all fields optional):
        description: New human-readable description.
        env: New environment variables dict (replaces existing).
        idle_ttl_s: New idle TTL in seconds.
        health_check_interval_s: New health check interval in seconds.

    Returns:
        JSON with {"provider_id": ..., "updated": true}, status 200.

    Raises:
        ProviderNotFoundError: If provider does not exist (-> 404).
    """
    provider_id = request.path_params["provider_id"]
    body = await request.json()
    result = await dispatch_command(
        UpdateProviderCommand(
            provider_id=provider_id,
            description=body.get("description"),
            env=body.get("env"),
            idle_ttl_s=body.get("idle_ttl_s"),
            health_check_interval_s=body.get("health_check_interval_s"),
            source="api",
        )
    )
    return HangarJSONResponse(result)


async def delete_provider(request: Request) -> HangarJSONResponse:
    """Delete a provider, stopping it first if running.

    Path params:
        provider_id: Provider identifier.

    Returns:
        JSON with {"provider_id": ..., "deleted": true}, status 200.

    Raises:
        ProviderNotFoundError: If provider does not exist (-> 404).
    """
    provider_id = request.path_params["provider_id"]
    result = await dispatch_command(
        DeleteProviderCommand(
            provider_id=provider_id,
            source="api",
        )
    )
    return HangarJSONResponse(result)


# Route definitions for mounting in the API router
provider_routes = [
    Route("/", list_providers, methods=["GET"]),
    Route("/", create_provider, methods=["POST"]),
    Route("/{provider_id:str}", get_provider, methods=["GET"]),
    Route("/{provider_id:str}", update_provider, methods=["PUT"]),
    Route("/{provider_id:str}", delete_provider, methods=["DELETE"]),
    Route("/{provider_id:str}/start", start_provider, methods=["POST"]),
    Route("/{provider_id:str}/stop", stop_provider, methods=["POST"]),
    Route("/{provider_id:str}/block", block_provider, methods=["POST"]),
    Route("/{provider_id:str}/tools", get_provider_tools, methods=["GET"]),
    Route("/{provider_id:str}/health", get_provider_health, methods=["GET"]),
    Route("/{provider_id:str}/logs", get_provider_logs, methods=["GET"]),
    Route("/{provider_id:str}/tools/history", get_provider_tool_history, methods=["GET"]),
]
