"""Query handlers for CQRS."""

from .queries import (
    GetProviderHealthQuery,
    GetProviderQuery,
    GetProviderToolsQuery,
    GetSystemMetricsQuery,
    ListProvidersQuery,
    Query,
    QueryHandler,
)

# Auth queries live in enterprise/auth/queries/.
# Re-export conditionally for backwards compatibility.
try:
    from enterprise.auth.queries.queries import (  # noqa: F401
        CheckPermissionQuery,
        GetApiKeyCountQuery,
        GetApiKeysByPrincipalQuery,
        GetRoleQuery,
        GetRolesForPrincipalQuery,
        ListBuiltinRolesQuery,
    )
except ImportError:
    pass


# Lazy import handlers to avoid circular imports
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
        try:
            from enterprise.auth.queries import handlers as auth_handlers

            return getattr(auth_handlers, name)
        except ImportError as err:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r} (enterprise not installed)") from err

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
    # Handlers
    "ListProvidersHandler",
    "GetProviderHandler",
    "GetProviderToolsHandler",
    "GetProviderHealthHandler",
    "GetSystemMetricsHandler",
    "register_all_handlers",
]
