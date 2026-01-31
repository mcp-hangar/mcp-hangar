"""Query handlers for CQRS."""

# Import base classes and query types first (no circular dependencies)
from .auth_queries import (
    CheckPermissionQuery,
    GetApiKeyCountQuery,
    GetApiKeysByPrincipalQuery,
    GetRoleQuery,
    GetRolesForPrincipalQuery,
    ListBuiltinRolesQuery,
)
from .queries import (
    GetProviderHealthQuery,
    GetProviderQuery,
    GetProviderToolsQuery,
    GetSystemMetricsQuery,
    ListProvidersQuery,
    Query,
    QueryHandler,
)


# Lazy import handlers to avoid circular imports
# These modules import from infrastructure.query_bus which imports from this module
def __getattr__(name: str):
    """Lazy import handlers to break circular dependency."""
    if name in (
        "GetProviderHandler",
        "GetProviderHealthHandler",
        "GetProviderToolsHandler",
        "GetSystemMetricsHandler",
        "ListProvidersHandler",
        "register_all_handlers",
    ):
        from . import handlers

        return getattr(handlers, name)

    if name in (
        "CheckPermissionHandler",
        "GetApiKeyCountHandler",
        "GetApiKeysByPrincipalHandler",
        "GetRoleHandler",
        "GetRolesForPrincipalHandler",
        "ListBuiltinRolesHandler",
        "register_auth_query_handlers",
    ):
        from . import auth_handlers

        return getattr(auth_handlers, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Query base classes
    "Query",
    "QueryHandler",
    # Provider Queries
    "ListProvidersQuery",
    "GetProviderQuery",
    "GetProviderToolsQuery",
    "GetProviderHealthQuery",
    "GetSystemMetricsQuery",
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
