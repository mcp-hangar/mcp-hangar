"""ASGI application factory and authentication middleware.

Provides functions to create ASGI applications with health endpoints
and optional authentication middleware.
"""

from typing import Any, TYPE_CHECKING

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from ..logging_config import get_logger

if TYPE_CHECKING:
    from ..server.auth_bootstrap import AuthComponents
    from .config import ServerConfig

logger = get_logger(__name__)


def create_health_routes(
    run_readiness_checks: callable,
    update_metrics: callable,
) -> list[Route]:
    """Create health, readiness, and metrics routes.

    Args:
        run_readiness_checks: Callable that returns readiness check results.
        update_metrics: Callable to update metrics before serving.

    Returns:
        List of Starlette Route objects.
    """
    from ..metrics import get_metrics

    async def health_endpoint(request):
        """Liveness endpoint (cheap ping)."""
        return JSONResponse({"status": "ok", "service": "mcp-hangar"})

    async def ready_endpoint(request):
        """Readiness endpoint with internal checks."""
        checks = run_readiness_checks()
        ready = all(v is True for k, v in checks.items() if isinstance(v, bool))
        return JSONResponse(
            {"ready": ready, "service": "mcp-hangar", "checks": checks},
            status_code=200 if ready else 503,
        )

    async def metrics_endpoint(request):
        """Prometheus metrics endpoint."""
        update_metrics()
        return PlainTextResponse(
            get_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return [
        Route("/health", health_endpoint, methods=["GET"]),
        Route("/ready", ready_endpoint, methods=["GET"]),
        Route("/metrics", metrics_endpoint, methods=["GET"]),
    ]


def create_combined_asgi_app(aux_app: Starlette, mcp_app: Any) -> Any:
    """Create combined ASGI app that routes to metrics/health or MCP.

    Args:
        aux_app: Starlette app for health/metrics endpoints.
        mcp_app: FastMCP ASGI app.

    Returns:
        Combined ASGI app callable.
    """

    async def combined_app(scope, receive, send):
        """Combined ASGI app that routes to metrics/health or MCP."""
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path in ("/health", "/ready", "/metrics"):
                await aux_app(scope, receive, send)
                return
        await mcp_app(scope, receive, send)

    return combined_app


def create_auth_combined_app(
    aux_app: Starlette,
    mcp_app: Any,
    auth_components: "AuthComponents",
    config: "ServerConfig",
) -> Any:
    """Create auth-enabled combined ASGI app.

    This wraps the MCP app with authentication middleware while
    keeping health/metrics endpoints unprotected.

    Args:
        aux_app: Starlette app for health/metrics endpoints.
        mcp_app: FastMCP ASGI app.
        auth_components: Authentication components from bootstrap_auth().
        config: Server configuration with auth settings.

    Returns:
        Combined ASGI app with auth middleware.
    """
    from ..domain.contracts.authentication import AuthRequest
    from ..domain.exceptions import AccessDeniedError, AuthenticationError

    skip_paths = set(config.auth_skip_paths)
    trusted_proxies = config.trusted_proxies

    async def auth_combined_app(scope, receive, send):
        """Combined ASGI app with authentication for MCP endpoints."""
        if scope["type"] != "http":
            # Non-HTTP (e.g., lifespan) - pass through
            await mcp_app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Skip auth for health/metrics endpoints
        if path in skip_paths:
            await aux_app(scope, receive, send)
            return

        # For MCP endpoints, apply authentication
        # Build headers dict from scope
        headers = {}
        for key, value in scope.get("headers", []):
            headers[key.decode("latin-1").lower()] = value.decode("latin-1")

        # Get client IP
        client = scope.get("client")
        source_ip = client[0] if client else "unknown"

        # Trust X-Forwarded-For only from trusted proxies
        if source_ip in trusted_proxies:
            forwarded_for = headers.get("x-forwarded-for")
            if forwarded_for:
                source_ip = forwarded_for.split(",")[0].strip()

        # Create auth request
        auth_request = AuthRequest(
            headers=headers,
            source_ip=source_ip,
            method=scope.get("method", ""),
            path=path,
        )

        try:
            # Authenticate
            auth_context = auth_components.authn_middleware.authenticate(auth_request)

            # Store auth context in scope for downstream handlers
            scope["auth"] = auth_context

            # Pass to MCP app
            await mcp_app(scope, receive, send)

        except AuthenticationError as e:
            response = JSONResponse(
                status_code=401,
                content={
                    "error": "authentication_failed",
                    "message": e.message,
                },
                headers={"WWW-Authenticate": "Bearer, ApiKey"},
            )
            await response(scope, receive, send)

        except AccessDeniedError as e:
            response = JSONResponse(
                status_code=403,
                content={
                    "error": "access_denied",
                    "message": str(e),
                },
            )
            await response(scope, receive, send)

    return auth_combined_app


__all__ = [
    "create_health_routes",
    "create_combined_asgi_app",
    "create_auth_combined_app",
]
