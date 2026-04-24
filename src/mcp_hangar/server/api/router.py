"""REST API router factory.

Creates a Starlette application with:
- CORSMiddleware configured from environment
- Optional AuthMiddlewareHTTP for enterprise authentication
- CSRFMiddleware for mutating browser-style requests
- TrustedHostMiddleware for host header validation
- Exception handlers mapping domain errors to JSON error envelopes
- McpServer endpoint routes mounted at /mcp_servers

Middleware ordering note: CORS is outermost for OPTIONS preflight handling,
auth runs inside CORS, CSRF runs outside auth, and TrustedHostMiddleware is innermost.
"""

# pyright: reportAny=false, reportExplicitAny=false

import os
from typing import Any

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import BaseRoute, Mount

from ...domain.exceptions import MCPError
from .middleware import AuthMiddlewareHTTP, CSRFMiddleware, error_handler, get_cors_config
from ..bootstrap.enterprise import get_enterprise_api_routes


def create_api_router(auth_components: Any = None) -> Starlette:
    """Create the REST API Starlette application.

    Returns a fully configured Starlette app with CORS middleware,
    error handlers, and all API endpoint routes mounted.

    When auth_components is provided and enabled, the enterprise
    AuthMiddlewareHTTP is mounted to protect all API routes.

    Args:
        auth_components: Optional enterprise auth components. When present
            and auth_components.enabled is True, authentication middleware
            is added to the application.

    Returns:
        Starlette application serving the REST API.
    """
    from .config import config_routes
    from .discovery import discovery_routes
    from .groups import group_routes
    from .mcp_servers import mcp_server_routes
    from .sessions import session_routes
    from .system import system_routes
    from .tools import tools_routes
    from .ws import ws_routes
    from .agent_policy import agent_policy_routes

    routes: list[BaseRoute] = [
        Mount("/mcp_servers", routes=mcp_server_routes),
        Mount("/sessions", routes=session_routes),
        Mount("/groups", routes=group_routes),
        Mount("/discovery", routes=discovery_routes),
        Mount("/config", routes=config_routes),
        Mount("/system", routes=system_routes),
        Mount("/tools", routes=tools_routes),
        Mount("/ws", routes=ws_routes),
        Mount("/agent/policy", routes=agent_policy_routes),
    ]

    routes.extend(get_enterprise_api_routes())

    exception_handlers = {
        MCPError: error_handler,
        Exception: error_handler,
    }

    app = Starlette(routes=routes, exception_handlers=exception_handlers)

    # TrustedHostMiddleware blocks unexpected Host headers (DNS rebinding protection).
    _trusted_hosts_env = os.environ.get("MCP_TRUSTED_HOSTS", "localhost,127.0.0.1,::1,testserver")
    trusted_hosts = [h.strip() for h in _trusted_hosts_env.split(",") if h.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    # CSRF middleware sits outside auth but inside CORS.
    # It enforces X-Requested-With for mutating browser requests while letting
    # non-browser API key and bearer token clients bypass the check.
    app.add_middleware(CSRFMiddleware)

    # Auth middleware: mount when auth is enabled.
    # Must be inside CORSMiddleware so CORS remains outermost (handles OPTIONS preflight first).
    if auth_components is not None and hasattr(auth_components, "enabled") and auth_components.enabled:
        app.add_middleware(AuthMiddlewareHTTP, authn=auth_components.authn_middleware)

    cors_config = get_cors_config()
    app.add_middleware(CORSMiddleware, **cors_config)

    return app
