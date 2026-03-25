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
    from .catalog import catalog_routes
    from .config import config_routes
    from .discovery import discovery_routes
    from .groups import group_routes
    from .maintenance import maintenance_routes
    from .observability import observability_routes
    from .providers import provider_routes
    from .system import system_routes
    from .ws import ws_routes

    # Behavioral report routes are part of enterprise tier.
    # Extend provider_routes so they share a single /providers Mount.
    try:
        from enterprise.behavioral.api.reports import behavioral_report_routes

        provider_routes.extend(behavioral_report_routes)
    except ImportError:
        pass

    routes = [
        Mount("/providers", routes=provider_routes),
        Mount("/groups", routes=group_routes),
        Mount("/discovery", routes=discovery_routes),
        Mount("/catalog", routes=catalog_routes),
        Mount("/config", routes=config_routes),
        Mount("/system", routes=system_routes),
        Mount("/observability", routes=observability_routes),
        Mount("/maintenance", routes=maintenance_routes),
        Mount("/ws", routes=ws_routes),
    ]

    # Auth routes are part of enterprise tier.
    try:
        from enterprise.auth.api.routes import auth_routes

        routes.append(Mount("/auth", routes=auth_routes))
    except ImportError:
        pass

    exception_handlers = {
        MCPError: error_handler,
        Exception: error_handler,
    }

    app = Starlette(routes=routes, exception_handlers=exception_handlers)

    cors_config = get_cors_config()
    app.add_middleware(CORSMiddleware, **cors_config)

    return app
