"""Authentication and Authorization query handlers.

Implements CQRS query handlers for auth read operations.
These handlers only read data, never modify state.
"""

from typing import Any

from ...domain.contracts.authentication import IApiKeyStore
from ...domain.contracts.authorization import IRoleStore
from ...domain.security.roles import BUILTIN_ROLES
from ...infrastructure.query_bus import QueryHandler
from ...logging_config import get_logger
from .auth_queries import (
    CheckPermissionQuery,
    GetApiKeyCountQuery,
    GetApiKeysByPrincipalQuery,
    GetRoleQuery,
    GetRolesForPrincipalQuery,
    ListBuiltinRolesQuery,
)

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


def register_auth_query_handlers(
    query_bus,
    api_key_store: IApiKeyStore | None = None,
    role_store: IRoleStore | None = None,
) -> None:
    """Register all auth query handlers with the query bus.

    Args:
        query_bus: QueryBus instance.
        api_key_store: API key store (optional, handlers skipped if None).
        role_store: Role store (optional, handlers skipped if None).
    """
    if api_key_store:
        query_bus.register(GetApiKeysByPrincipalQuery, GetApiKeysByPrincipalHandler(api_key_store))
        query_bus.register(GetApiKeyCountQuery, GetApiKeyCountHandler(api_key_store))
        logger.info("auth_api_key_query_handlers_registered")

    if role_store:
        query_bus.register(GetRolesForPrincipalQuery, GetRolesForPrincipalHandler(role_store))
        query_bus.register(GetRoleQuery, GetRoleHandler(role_store))
        query_bus.register(CheckPermissionQuery, CheckPermissionHandler(role_store))
        logger.info("auth_role_query_handlers_registered")

    # ListBuiltinRolesQuery doesn't need a store
    query_bus.register(ListBuiltinRolesQuery, ListBuiltinRolesHandler())
    logger.info("auth_builtin_roles_query_handler_registered")
