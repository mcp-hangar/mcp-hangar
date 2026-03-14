"""Auth management endpoint handlers for the REST API.

Implements API key and role management endpoints, routing through
the CQRS dispatch helpers to registered auth handlers.
"""

from starlette.requests import Request
from starlette.routing import Route

from ...application.commands.auth_commands import (
    AssignRoleCommand,
    CreateApiKeyCommand,
    CreateCustomRoleCommand,
    RevokeApiKeyCommand,
    RevokeRoleCommand,
)
from ...application.queries.auth_queries import (
    GetApiKeysByPrincipalQuery,
    GetRolesForPrincipalQuery,
    ListBuiltinRolesQuery,
)
from .middleware import dispatch_command, dispatch_query
from .serializers import HangarJSONResponse


async def create_api_key(request: Request) -> HangarJSONResponse:
    """Create an API key for a principal.

    Request body:
        principal_id: Principal this key authenticates as.
        name: Human-readable name for the key.
        created_by: Optional principal creating the key (default "system").
        expires_at: Optional ISO8601 expiry datetime string.

    Returns:
        JSON with key_id, raw_key (shown once!), principal_id, name.
    """
    body = await request.json()
    expires_at = None
    if body.get("expires_at"):
        from datetime import datetime

        expires_at = datetime.fromisoformat(body["expires_at"])

    result = await dispatch_command(
        CreateApiKeyCommand(
            principal_id=body["principal_id"],
            name=body["name"],
            created_by=body.get("created_by", "system"),
            expires_at=expires_at,
        )
    )
    return HangarJSONResponse(result, status_code=201)


async def revoke_api_key(request: Request) -> HangarJSONResponse:
    """Revoke an API key.

    Path params:
        key_id: Key identifier.

    Request body (optional JSON):
        revoked_by: Principal revoking the key (default "system").
        reason: Optional reason string.

    Returns:
        JSON with revocation status.
    """
    key_id = request.path_params["key_id"]
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    result = await dispatch_command(
        RevokeApiKeyCommand(
            key_id=key_id,
            revoked_by=body.get("revoked_by", "system"),
            reason=body.get("reason", ""),
        )
    )
    return HangarJSONResponse(result)


async def list_api_keys(request: Request) -> HangarJSONResponse:
    """List API keys for a principal.

    Query params:
        principal_id: Required. Principal whose keys to list.
        include_revoked: Optional bool (default true).

    Returns:
        JSON with {"principal_id": ..., "keys": [...], "total": int, "active": int}.
    """
    principal_id = request.query_params.get("principal_id", "")
    include_revoked = request.query_params.get("include_revoked", "true").lower() != "false"
    result = await dispatch_query(
        GetApiKeysByPrincipalQuery(
            principal_id=principal_id,
            include_revoked=include_revoked,
        )
    )
    return HangarJSONResponse(result)


async def assign_role(request: Request) -> HangarJSONResponse:
    """Assign a role to a principal.

    Request body:
        principal_id: Principal receiving the role.
        role_name: Role to assign.
        scope: Optional scope (default "global").
        assigned_by: Optional assigner (default "system").

    Returns:
        JSON with assignment status.
    """
    body = await request.json()
    result = await dispatch_command(
        AssignRoleCommand(
            principal_id=body["principal_id"],
            role_name=body["role_name"],
            scope=body.get("scope", "global"),
            assigned_by=body.get("assigned_by", "system"),
        )
    )
    return HangarJSONResponse(result)


async def revoke_role(request: Request) -> HangarJSONResponse:
    """Revoke a role from a principal.

    Request body:
        principal_id: Principal losing the role.
        role_name: Role to revoke.
        scope: Optional scope (default "global").
        revoked_by: Optional revoker (default "system").

    Returns:
        JSON with revocation status.
    """
    body = await request.json()
    result = await dispatch_command(
        RevokeRoleCommand(
            principal_id=body["principal_id"],
            role_name=body["role_name"],
            scope=body.get("scope", "global"),
            revoked_by=body.get("revoked_by", "system"),
        )
    )
    return HangarJSONResponse(result)


async def list_roles(request: Request) -> HangarJSONResponse:
    """List all built-in roles.

    Returns:
        JSON with {"roles": [...role dicts]}.
    """
    result = await dispatch_query(ListBuiltinRolesQuery())
    return HangarJSONResponse(result)


async def create_custom_role(request: Request) -> HangarJSONResponse:
    """Create a custom role.

    Request body:
        role_name: Unique name for the role.
        description: Optional human-readable description.
        permissions: Optional list of permission strings (format: "resource:action:id").
        created_by: Optional creator principal (default "system").

    Returns:
        JSON with created role info.
    """
    body = await request.json()
    result = await dispatch_command(
        CreateCustomRoleCommand(
            role_name=body["role_name"],
            description=body.get("description", ""),
            permissions=frozenset(body.get("permissions", [])),
            created_by=body.get("created_by", "system"),
        )
    )
    return HangarJSONResponse(result, status_code=201)


async def get_principal_roles(request: Request) -> HangarJSONResponse:
    """Get roles assigned to a principal.

    Query params:
        principal_id: Required. Principal whose roles to list.
        scope: Optional scope filter (default "*" = all).

    Returns:
        JSON with {"principal_id": ..., "roles": [...]}.
    """
    principal_id = request.query_params.get("principal_id", "")
    scope = request.query_params.get("scope", "*")
    result = await dispatch_query(
        GetRolesForPrincipalQuery(
            principal_id=principal_id,
            scope=scope,
        )
    )
    return HangarJSONResponse(result)


# Route definitions for mounting in the API router
auth_routes = [
    Route("/keys", create_api_key, methods=["POST"]),
    Route("/keys", list_api_keys, methods=["GET"]),
    Route("/keys/{key_id:str}", revoke_api_key, methods=["DELETE"]),
    Route("/roles", list_roles, methods=["GET"]),
    Route("/roles", create_custom_role, methods=["POST"]),
    Route("/roles/assign", assign_role, methods=["POST"]),
    Route("/roles/revoke", revoke_role, methods=["DELETE"]),
    Route("/principals/roles", get_principal_roles, methods=["GET"]),
]
