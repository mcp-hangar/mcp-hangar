"""Auth management endpoint handlers for the REST API.

Implements API key and role management endpoints, routing through
the CQRS dispatch helpers to registered auth handlers.
"""

import json

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...application.commands.auth_commands import (
    AssignRoleCommand,
    ClearToolAccessPolicyCommand,
    CreateApiKeyCommand,
    CreateCustomRoleCommand,
    DeleteCustomRoleCommand,
    RevokeApiKeyCommand,
    RevokeRoleCommand,
    SetToolAccessPolicyCommand,
    UpdateCustomRoleCommand,
)
from ...application.queries.auth_queries import (
    CheckPermissionQuery,
    GetApiKeysByPrincipalQuery,
    GetRoleQuery,
    GetRolesForPrincipalQuery,
    GetToolAccessPolicyQuery,
    ListAllRolesQuery,
    ListBuiltinRolesQuery,
    ListPrincipalsQuery,
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
    except (json.JSONDecodeError, ValueError):
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


# =============================================================================
# Phase 27: Role Management (RBAC-02)
# =============================================================================


async def list_all_roles(request: Request) -> HangarJSONResponse:
    """List all roles (builtin and custom).

    Query params:
        include_builtin: Optional bool (default true). Set to "false" to omit builtin roles.

    Returns:
        JSON with {"roles": [...], "total": int, "builtin_count": int, "custom_count": int}.
    """
    include_builtin = request.query_params.get("include_builtin", "true").lower() != "false"
    result = await dispatch_query(ListAllRolesQuery(include_builtin=include_builtin))
    return HangarJSONResponse(result)


async def get_role(request: Request) -> HangarJSONResponse:
    """Get a specific role by name.

    Path params:
        role_name: Name of the role.

    Returns:
        JSON with role details, or 404 if not found.
    """
    role_name = request.path_params["role_name"]
    result = await dispatch_query(GetRoleQuery(role_name=role_name))
    if not result.get("found"):
        return HangarJSONResponse(
            {"error": {"code": "RoleNotFoundError", "message": f"Role not found: {role_name}"}},
            status_code=404,
        )
    return HangarJSONResponse(result)


async def delete_role(request: Request) -> Response:
    """Delete a custom role.

    Path params:
        role_name: Name of the role to delete.

    Returns:
        204 No Content on success, 403 if builtin, 404 if not found.
    """
    role_name = request.path_params["role_name"]
    await dispatch_command(DeleteCustomRoleCommand(role_name=role_name))
    return Response(status_code=204)


async def update_role(request: Request) -> HangarJSONResponse:
    """Update a custom role's permissions and description.

    Path params:
        role_name: Name of the role to update.

    Request body:
        permissions: List of permission strings (format "resource:action:id").
        description: Optional new description string.
        updated_by: Optional principal making the update (default "system").

    Returns:
        JSON with updated role info.
    """
    role_name = request.path_params["role_name"]
    body = await request.json()
    result = await dispatch_command(
        UpdateCustomRoleCommand(
            role_name=role_name,
            permissions=body.get("permissions", []),
            description=body.get("description"),
            updated_by=body.get("updated_by", "system"),
        )
    )
    return HangarJSONResponse(result)


async def list_principals(request: Request) -> HangarJSONResponse:
    """List all principals with at least one role assignment.

    Returns:
        JSON with {"principals": [...], "total": int}.
    """
    result = await dispatch_query(ListPrincipalsQuery())
    return HangarJSONResponse(result)


async def list_permissions(request: Request) -> HangarJSONResponse:
    """List all available permission actions/resource types.

    Returns a static manifest of all permission strings supported by the system.
    No query needed -- this is read from domain constants.

    Returns:
        JSON with {"permissions": [{"resource_type": ..., "actions": [...]}]}.
    """
    from ...domain.value_objects.security import ACTIONS, RESOURCE_TYPES

    # Graceful fallback: if constants don't exist, return known defaults
    try:
        permissions = [{"resource_type": rt, "actions": list(ACTIONS)} for rt in RESOURCE_TYPES]
    except Exception:  # noqa: BLE001
        permissions = [
            {"resource_type": "provider", "actions": ["read", "write", "invoke", "admin"]},
            {"resource_type": "group", "actions": ["read", "write", "admin"]},
            {"resource_type": "tool", "actions": ["invoke", "read"]},
            {"resource_type": "config", "actions": ["read", "write"]},
            {"resource_type": "*", "actions": ["*"]},
        ]

    return HangarJSONResponse({"permissions": permissions})


async def check_permission(request: Request) -> HangarJSONResponse:
    """Check if a principal has a specific permission.

    Request body:
        principal_id: Principal to check.
        action: Action being requested (e.g. "invoke").
        resource_type: Resource type (e.g. "tool").
        resource_id: Specific resource ID (default "*").

    Returns:
        JSON with {"allowed": bool, "granted_by_role": str | null}.
    """
    body = await request.json()
    result = await dispatch_query(
        CheckPermissionQuery(
            principal_id=body["principal_id"],
            action=body["action"],
            resource_type=body["resource_type"],
            resource_id=body.get("resource_id", "*"),
        )
    )
    return HangarJSONResponse(result)


# =============================================================================
# Phase 27: Tool Access Policy (TAP-01)
# =============================================================================

_VALID_TAP_SCOPES = frozenset({"provider", "group", "member"})


async def set_tool_access_policy(request: Request) -> HangarJSONResponse:
    """Set (upsert) a tool access policy for a scope/target.

    Path params:
        scope: One of "provider", "group", or "member".
        target_id: Identifier of the provider, group, or member.

    Request body:
        allow_list: List of tool name patterns to allow (default []).
        deny_list: List of tool name patterns to deny (default []).

    Returns:
        JSON with confirmation on success, 400 for invalid scope.
    """
    scope = request.path_params["scope"]
    target_id = request.path_params["target_id"]

    if scope not in _VALID_TAP_SCOPES:
        return HangarJSONResponse(
            {
                "error": {
                    "code": "ValidationError",
                    "message": f"Invalid scope '{scope}'. Must be one of: provider, group, member.",
                }
            },
            status_code=400,
        )

    body = await request.json()
    result = await dispatch_command(
        SetToolAccessPolicyCommand(
            scope=scope,
            target_id=target_id,
            allow_list=body.get("allow_list", []),
            deny_list=body.get("deny_list", []),
        )
    )
    return HangarJSONResponse(result)


async def get_tool_access_policy(request: Request) -> HangarJSONResponse:
    """Get the tool access policy for a scope/target.

    Path params:
        scope: One of "provider", "group", or "member".
        target_id: Identifier of the provider, group, or member.

    Returns:
        JSON with policy details, or 404 when no policy exists.
    """
    scope = request.path_params["scope"]
    target_id = request.path_params["target_id"]

    if scope not in _VALID_TAP_SCOPES:
        return HangarJSONResponse(
            {
                "error": {
                    "code": "ValidationError",
                    "message": f"Invalid scope '{scope}'. Must be one of: provider, group, member.",
                }
            },
            status_code=400,
        )

    result = await dispatch_query(GetToolAccessPolicyQuery(scope=scope, target_id=target_id))

    if not result.get("found"):
        return HangarJSONResponse(
            {
                "error": {
                    "code": "PolicyNotFound",
                    "message": f"No tool access policy found for scope='{scope}', target_id='{target_id}'.",
                }
            },
            status_code=404,
        )

    return HangarJSONResponse(result)


async def clear_tool_access_policy(request: Request) -> Response:
    """Clear (remove) the tool access policy for a scope/target.

    Path params:
        scope: One of "provider", "group", or "member".
        target_id: Identifier of the provider, group, or member.

    Returns:
        204 No Content on success, 400 for invalid scope.
    """
    scope = request.path_params["scope"]
    target_id = request.path_params["target_id"]

    if scope not in _VALID_TAP_SCOPES:
        return HangarJSONResponse(
            {
                "error": {
                    "code": "ValidationError",
                    "message": f"Invalid scope '{scope}'. Must be one of: provider, group, member.",
                }
            },
            status_code=400,
        )

    await dispatch_command(ClearToolAccessPolicyCommand(scope=scope, target_id=target_id))
    return Response(status_code=204)


# Route definitions for mounting in the API router.
# ORDERING RULE: exact-match paths must come before parameterised paths.
# E.g., "/roles/all" and "/roles/assign" must precede "/roles/{role_name}".
auth_routes = [
    # API key management
    Route("/keys", create_api_key, methods=["POST"]),
    Route("/keys", list_api_keys, methods=["GET"]),
    Route("/keys/{key_id:str}", revoke_api_key, methods=["DELETE"]),
    # Role management -- exact-match routes first
    Route("/roles", list_roles, methods=["GET"]),
    Route("/roles", create_custom_role, methods=["POST"]),
    Route("/roles/all", list_all_roles, methods=["GET"]),
    Route("/roles/assign", assign_role, methods=["POST"]),
    Route("/roles/revoke", revoke_role, methods=["DELETE"]),
    # Parameterised role routes after exact-match
    Route("/roles/{role_name:str}", get_role, methods=["GET"]),
    Route("/roles/{role_name:str}", delete_role, methods=["DELETE"]),
    Route("/roles/{role_name:str}", update_role, methods=["PATCH"]),
    # Principal and permission management
    Route("/principals", list_principals, methods=["GET"]),
    Route("/principals/roles", get_principal_roles, methods=["GET"]),
    Route("/permissions", list_permissions, methods=["GET"]),
    Route("/check-permission", check_permission, methods=["POST"]),
    # Tool access policy management
    Route("/policies/{scope:str}/{target_id:str}", set_tool_access_policy, methods=["POST"]),
    Route("/policies/{scope:str}/{target_id:str}", get_tool_access_policy, methods=["GET"]),
    Route("/policies/{scope:str}/{target_id:str}", clear_tool_access_policy, methods=["DELETE"]),
]
