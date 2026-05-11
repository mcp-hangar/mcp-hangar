"""Query handlers for CQRS."""

import importlib

from .queries import (
    GetMcpServerHealthQuery,
    GetMcpServerQuery,
    GetMcpServerToolsQuery,
    GetSystemMetricsQuery,
    ListMcpServersQuery,
    Query,
    QueryHandler,
)

_ENTERPRISE_AUTH_QUERIES = {
    "CheckPermissionQuery",
    "GetApiKeyCountQuery",
    "GetApiKeysByPrincipalQuery",
    "GetRoleQuery",
    "GetRolesForPrincipalQuery",
    "ListBuiltinRolesQuery",
}

_ENTERPRISE_AUTH_QUERY_HANDLERS = {
    "CheckPermissionHandler",
    "GetApiKeyCountHandler",
    "GetApiKeysByPrincipalHandler",
    "GetRoleHandler",
    "GetRolesForPrincipalHandler",
    "ListBuiltinRolesHandler",
    "register_auth_query_handlers",
}


def __getattr__(name: str):  # noqa: ANN001
    if name in (
        "GetMcpServerHandler",
        "GetMcpServerHealthHandler",
        "GetMcpServerToolsHandler",
        "GetSystemMetricsHandler",
        "ListMcpServersHandler",
        "register_all_handlers",
    ):
        from . import handlers

        return getattr(handlers, name)

    if name in _ENTERPRISE_AUTH_QUERIES:
        try:
            return getattr(importlib.import_module("mcp_hangar.auth.queries.queries"), name)
        except ImportError as err:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r} (enterprise not installed)") from err

    if name in _ENTERPRISE_AUTH_QUERY_HANDLERS:
        try:
            return getattr(importlib.import_module("mcp_hangar.auth.queries.handlers"), name)
        except ImportError as err:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r} (enterprise not installed)") from err

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Query base classes
    "Query",
    "QueryHandler",
    # McpServer Queries
    "ListMcpServersQuery",
    "GetMcpServerQuery",
    "GetMcpServerToolsQuery",
    "GetMcpServerHealthQuery",
    "GetSystemMetricsQuery",
    # Handlers
    "ListMcpServersHandler",
    "GetMcpServerHandler",
    "GetMcpServerToolsHandler",
    "GetMcpServerHealthHandler",
    "GetSystemMetricsHandler",
    "register_all_handlers",
]
