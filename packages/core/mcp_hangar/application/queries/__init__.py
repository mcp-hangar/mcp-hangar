"""Query handlers for CQRS."""

from .auth_handlers import (
    CheckPermissionHandler,
    GetApiKeyCountHandler,
    GetApiKeysByPrincipalHandler,
    GetRoleHandler,
    GetRolesForPrincipalHandler,
    ListBuiltinRolesHandler,
    register_auth_query_handlers,
)
from .auth_queries import (
    CheckPermissionQuery,
    GetApiKeyCountQuery,
    GetApiKeysByPrincipalQuery,
    GetRoleQuery,
    GetRolesForPrincipalQuery,
    ListBuiltinRolesQuery,
)
from .handlers import (
    GetProviderHandler,
    GetProviderHealthHandler,
    GetProviderToolsHandler,
    GetSystemMetricsHandler,
    ListProvidersHandler,
    register_all_handlers,
)

__all__ = [
    # Provider Query Handlers
    "ListProvidersHandler",
    "GetProviderHandler",
    "GetProviderToolsHandler",
    "GetProviderHealthHandler",
    "GetSystemMetricsHandler",
    "register_all_handlers",
    # Auth Queries
    "GetApiKeysByPrincipalQuery",
    "GetApiKeyCountQuery",
    "GetRolesForPrincipalQuery",
    "GetRoleQuery",
    "ListBuiltinRolesQuery",
    "CheckPermissionQuery",
    # Auth Query Handlers
    "GetApiKeysByPrincipalHandler",
    "GetApiKeyCountHandler",
    "GetRolesForPrincipalHandler",
    "GetRoleHandler",
    "ListBuiltinRolesHandler",
    "CheckPermissionHandler",
    "register_auth_query_handlers",
]
