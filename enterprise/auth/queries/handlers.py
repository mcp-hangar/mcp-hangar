"""Authentication and Authorization query handlers.

Implements CQRS query handlers for auth read operations.
These handlers only read data, never modify state.
"""

from typing import Any

from mcp_hangar.domain.contracts.authentication import IApiKeyStore
from mcp_hangar.domain.contracts.authorization import IRoleStore
from enterprise.auth.roles import BUILTIN_ROLES
from mcp_hangar.logging_config import get_logger
from enterprise.auth.queries.queries import (
    CheckPermissionQuery,
    GetApiKeyCountQuery,
    GetApiKeysByPrincipalQuery,
    GetRoleQuery,
    GetRolesForPrincipalQuery,
    GetToolAccessPolicyQuery,
    ListAllRolesQuery,
    ListBuiltinRolesQuery,
    ListPrincipalsQuery,
)
from mcp_hangar.application.queries.queries import QueryHandler

logger = get_logger(__name__)


# =============================================================================
# API Key Query Handlers
# =============================================================================


class GetApiKeysByPrincipalHandler(QueryHandler):
    """Handler for GetApiKeysByPrincipalQuery."""

    def __init__(self, api_key_store: IApiKeyStore):
        self._store = api_key_store

    def handle(self, query: GetApiKeysByPrincipalQuery) -> dict[str, Any]:
        """Get all API keys for a principal.

        Returns:
            Dict with list of key metadata.
        """
        keys = self._store.list_keys(query.principal_id)

        if not query.include_revoked:
            keys = [k for k in keys if not k.revoked]

        return {
            "principal_id": query.principal_id,
            "keys": [
                {
                    "key_id": k.key_id,
                    "name": k.name,
                    "created_at": k.created_at.isoformat() if k.created_at else None,
                    "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                    "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                    "revoked": k.revoked,
                }
                for k in keys
            ],
            "total": len(keys),
            "active": sum(1 for k in keys if not k.revoked),
        }


class GetApiKeyCountHandler(QueryHandler):
    """Handler for GetApiKeyCountQuery."""

    def __init__(self, api_key_store: IApiKeyStore):
        self._store = api_key_store

    def handle(self, query: GetApiKeyCountQuery) -> dict[str, Any]:
        """Get count of active API keys for a principal.

        Returns:
            Dict with key count.
        """
        count = self._store.count_keys(query.principal_id)

        return {
            "principal_id": query.principal_id,
            "active_keys": count,
        }


# =============================================================================
# Role Query Handlers
# =============================================================================


class GetRolesForPrincipalHandler(QueryHandler):
    """Handler for GetRolesForPrincipalQuery."""

    def __init__(self, role_store: IRoleStore):
        self._store = role_store

    def handle(self, query: GetRolesForPrincipalQuery) -> dict[str, Any]:
        """Get all roles assigned to a principal.

        Returns:
            Dict with list of roles.
        """
        roles = self._store.get_roles_for_principal(
            principal_id=query.principal_id,
            scope=query.scope,
        )

        return {
            "principal_id": query.principal_id,
            "scope": query.scope,
            "roles": [
                {
                    "name": r.name,
                    "description": r.description,
                    "permissions": [str(p) for p in r.permissions],
                }
                for r in roles
            ],
            "count": len(roles),
        }


class GetRoleHandler(QueryHandler):
    """Handler for GetRoleQuery."""

    def __init__(self, role_store: IRoleStore):
        self._store = role_store

    def handle(self, query: GetRoleQuery) -> dict[str, Any]:
        """Get a specific role by name.

        Returns:
            Dict with role details or None.
        """
        role = self._store.get_role(query.role_name)

        if role is None:
            return {"role": None, "found": False}

        return {
            "found": True,
            "role": {
                "name": role.name,
                "description": role.description,
                "permissions": [str(p) for p in role.permissions],
                "permissions_count": len(role.permissions),
            },
        }


class ListBuiltinRolesHandler(QueryHandler):
    """Handler for ListBuiltinRolesQuery."""

    def handle(self, query: ListBuiltinRolesQuery) -> dict[str, Any]:
        """List all built-in roles.

        Returns:
            Dict with list of built-in roles.
        """
        return {
            "roles": [
                {
                    "name": name,
                    "description": role.description,
                    "permissions_count": len(role.permissions),
                }
                for name, role in BUILTIN_ROLES.items()
            ],
            "count": len(BUILTIN_ROLES),
        }


class CheckPermissionHandler(QueryHandler):
    """Handler for CheckPermissionQuery."""

    def __init__(self, role_store: IRoleStore):
        self._store = role_store

    def handle(self, query: CheckPermissionQuery) -> dict[str, Any]:
        """Check if a principal has a specific permission.

        Returns:
            Dict with permission check result.
        """
        roles = self._store.get_roles_for_principal(query.principal_id)

        for role in roles:
            if role.has_permission(
                resource_type=query.resource_type,
                action=query.action,
                resource_id=query.resource_id,
            ):
                return {
                    "principal_id": query.principal_id,
                    "action": query.action,
                    "resource_type": query.resource_type,
                    "resource_id": query.resource_id,
                    "allowed": True,
                    "granted_by_role": role.name,
                }

        return {
            "principal_id": query.principal_id,
            "action": query.action,
            "resource_type": query.resource_type,
            "resource_id": query.resource_id,
            "allowed": False,
            "granted_by_role": None,
        }


class ListAllRolesHandler(QueryHandler):
    """Handler for ListAllRolesQuery.

    Returns all roles (builtin and/or custom) from the role store.
    """

    def __init__(self, role_store: IRoleStore):
        self._store = role_store

    def handle(self, query: ListAllRolesQuery) -> dict[str, Any]:
        """List all roles.

        Returns:
            Dict with "roles" list.
        """
        custom_roles = self._store.list_all_roles()

        if query.include_builtin:
            builtin = [
                {
                    "name": name,
                    "description": role.description,
                    "permissions": [str(p) for p in role.permissions],
                    "permissions_count": len(role.permissions),
                    "is_builtin": True,
                }
                for name, role in BUILTIN_ROLES.items()
            ]
        else:
            builtin = []

        custom = [
            {
                "name": r.name,
                "description": r.description,
                "permissions": [str(p) for p in r.permissions],
                "permissions_count": len(r.permissions),
                "is_builtin": False,
            }
            for r in custom_roles
        ]

        all_roles = builtin + custom
        return {
            "roles": all_roles,
            "total": len(all_roles),
            "builtin_count": len(builtin),
            "custom_count": len(custom),
        }


class ListPrincipalsHandler(QueryHandler):
    """Handler for ListPrincipalsQuery.

    Returns all principals that have at least one role assignment.
    Uses InMemoryRoleStore._assignments or SQLiteRoleStore role_assignments table
    via a new list_principals() method. Falls back to scanning known assignments
    if the store does not expose a dedicated method.
    """

    def __init__(self, role_store: IRoleStore):
        self._store = role_store

    def handle(self, query: ListPrincipalsQuery) -> dict[str, Any]:
        """List principals with at least one role assignment.

        Returns:
            Dict with "principals" list; each item has principal_id and roles.
        """
        # Use list_principals() if available (SQLiteRoleStore); otherwise
        # fall back to the _assignments dict (InMemoryRoleStore)
        if hasattr(self._store, "list_principals"):
            principals_data = self._store.list_principals()
        elif hasattr(self._store, "_assignments"):
            # InMemoryRoleStore: _assignments[principal_id][scope] = set[role_names]
            principals_data = []
            for principal_id, scope_map in self._store._assignments.items():
                all_roles: list[str] = []
                for role_set in scope_map.values():
                    all_roles.extend(role_set)
                if all_roles:
                    principals_data.append({"principal_id": principal_id, "roles": sorted(set(all_roles))})
        else:
            principals_data = []

        return {
            "principals": principals_data,
            "total": len(principals_data),
        }


class GetToolAccessPolicyHandler(QueryHandler):
    """Handler for GetToolAccessPolicyQuery.

    Retrieves the stored tool access policy for a given scope/target.
    Returns {"found": False} if no policy is stored.
    """

    def __init__(self, tap_store: Any):
        self._tap_store = tap_store

    def handle(self, query: GetToolAccessPolicyQuery) -> dict[str, Any]:
        """Get the tool access policy for a scope/target.

        Returns:
            Dict with "found" bool and policy details when found.
        """
        policy = self._tap_store.get_policy(query.scope, query.target_id)

        if policy is None:
            return {
                "found": False,
                "scope": query.scope,
                "target_id": query.target_id,
                "allow_list": [],
                "deny_list": [],
            }

        return {
            "found": True,
            "scope": query.scope,
            "target_id": query.target_id,
            "allow_list": list(policy.allow_list),
            "deny_list": list(policy.deny_list),
        }


def register_auth_query_handlers(
    query_bus,
    api_key_store: IApiKeyStore | None = None,
    role_store: IRoleStore | None = None,
    tap_store: Any = None,
) -> None:
    """Register all auth query handlers with the query bus.

    Args:
        query_bus: QueryBus instance.
        api_key_store: API key store (optional, handlers skipped if None).
        role_store: Role store (optional, handlers skipped if None).
        tap_store: Tool access policy store (optional, TAP query handler skipped if None).
    """
    if api_key_store:
        query_bus.register(GetApiKeysByPrincipalQuery, GetApiKeysByPrincipalHandler(api_key_store))
        query_bus.register(GetApiKeyCountQuery, GetApiKeyCountHandler(api_key_store))
        logger.info("auth_api_key_query_handlers_registered")

    if role_store:
        query_bus.register(GetRolesForPrincipalQuery, GetRolesForPrincipalHandler(role_store))
        query_bus.register(GetRoleQuery, GetRoleHandler(role_store))
        query_bus.register(CheckPermissionQuery, CheckPermissionHandler(role_store))
        query_bus.register(ListAllRolesQuery, ListAllRolesHandler(role_store))
        query_bus.register(ListPrincipalsQuery, ListPrincipalsHandler(role_store))
        logger.info("auth_role_query_handlers_registered")

    if tap_store:
        query_bus.register(GetToolAccessPolicyQuery, GetToolAccessPolicyHandler(tap_store))
        logger.info("auth_tap_query_handler_registered")

    # ListBuiltinRolesQuery doesn't need a store
    query_bus.register(ListBuiltinRolesQuery, ListBuiltinRolesHandler())
    logger.info("auth_builtin_roles_query_handler_registered")
