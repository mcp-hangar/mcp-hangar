"""REST API router factory.

Creates a Starlette application with:
- CORSMiddleware configured from environment
- Exception handlers mapping domain errors to JSON error envelopes
- Provider endpoint routes mounted at /providers
"""

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from ...domain.exceptions import MCPError
from .middleware import error_handler, get_cors_config


def create_api_router() -> Starlette:
    """Create the REST API Starlette application.

    Returns a fully configured Starlette app with CORS middleware,
    error handlers, and all API endpoint routes mounted.

    Returns:
        Starlette application serving the REST API.
    """
    from .auth import auth_routes
    from .config import config_routes
    from .discovery import discovery_routes
    from .groups import group_routes
    from .observability import observability_routes
    from .providers import provider_routes
    from .system import system_routes
    from .ws import ws_routes

    routes = [
        Mount("/providers", routes=provider_routes),
        Mount("/groups", routes=group_routes),
        Mount("/discovery", routes=discovery_routes),
        Mount("/config", routes=config_routes),
        Mount("/system", routes=system_routes),
        Mount("/auth", routes=auth_routes),
        Mount("/observability", routes=observability_routes),
        Mount("/ws", routes=ws_routes),
    ]

    exception_handlers = {
        MCPError: error_handler,
        Exception: error_handler,
    }

    app = Starlette(routes=routes, exception_handlers=exception_handlers)

    cors_config = get_cors_config()
    app.add_middleware(CORSMiddleware, **cors_config)

    return app
