"""ASGI application factory and authentication middleware.

Provides functions to create ASGI applications with health endpoints
and optional authentication middleware.
"""

import os
from pathlib import Path
from typing import Any, TYPE_CHECKING

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from ..logging_config import get_logger

if TYPE_CHECKING:
    from mcp_hangar.server.bootstrap import AuthComponents
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


def resolve_ui_dist(override: Path | None = None) -> Path | None:
    """Resolve the UI static files directory.

    Checks (in order): explicit override, MCP_UI_DIST env var, auto-detect from
    the repo layout (packages/ui/dist relative to this file's location).

    Args:
        override: Explicit path override (highest priority).

    Returns:
        Path to a valid UI dist directory (contains index.html), or None.
    """
    if override is not None:
        return override if (override / "index.html").exists() else None

    env_val = os.environ.get("MCP_UI_DIST")
    if env_val:
        p = Path(env_val)
        return p if (p / "index.html").exists() else None

    # Auto-detect: this file is packages/core/mcp_hangar/fastmcp_server/asgi.py
    # Going up 4 levels reaches the repo root; then packages/ui/dist
    candidate = Path(__file__).parents[4] / "packages" / "ui" / "dist"
    if (candidate / "index.html").exists():
        return candidate

    return None


def make_spa_handler(ui_dist: Path) -> Any:
    """Create an ASGI handler that serves Vite static files with SPA fallback.

    Routes:
        /assets/* -> StaticFiles from ui_dist/assets/
        Known static file extensions (ico, png, svg, etc.) -> StaticFiles from ui_dist/
        Everything else -> FileResponse(ui_dist/index.html)

    Args:
        ui_dist: Path to Vite build output directory (must contain index.html).

    Returns:
        ASGI callable.
    """
    from starlette.responses import FileResponse
    from starlette.staticfiles import StaticFiles

    assets_dir = ui_dist / "assets"
    assets_app: Any = StaticFiles(directory=str(assets_dir)) if assets_dir.is_dir() else None
    root_static: Any = StaticFiles(directory=str(ui_dist)) if ui_dist.is_dir() else None
    index_html = ui_dist / "index.html"

    # Extensions that Vite places at the root level (not inside /assets/)
    static_root_exts = {".ico", ".png", ".svg", ".webmanifest", ".json", ".txt", ".xml"}

    async def spa_handler(scope: dict, receive: Any, send: Any) -> None:
        path: str = scope.get("path", "/")
        filename = path.split("/")[-1]
        suffix = Path(filename).suffix.lower() if "." in filename else ""

        if path.startswith("/assets/") and assets_app is not None:
            sub_scope = dict(scope)
            sub_scope["path"] = path[len("/assets") :]
            sub_scope["root_path"] = scope.get("root_path", "") + "/assets"
            try:
                await assets_app(sub_scope, receive, send)
                return
            except Exception:  # noqa: BLE001 -- fall through to index.html on any static error
                pass

        if suffix in static_root_exts and root_static is not None:
            try:
                await root_static(scope, receive, send)
                return
            except Exception:  # noqa: BLE001 -- fall through to index.html on any static error
                pass

        # SPA fallback: serve index.html for all unmatched routes
        response = FileResponse(str(index_html))
        await response(scope, receive, send)

    return spa_handler


def create_combined_asgi_app(
    aux_app: Starlette,
    mcp_app: Any,
    api_app: Any = None,
    ui_dist: Path | None = None,
) -> Any:
    """Create combined ASGI app that routes to metrics/health, REST API, UI static files, or MCP.

    Args:
        aux_app: Starlette app for health/metrics endpoints.
        mcp_app: FastMCP ASGI app.
        api_app: Optional REST API ASGI app mounted at /api/.
        ui_dist: Optional path to Vite build output. When set, non-API HTTP
            requests are served as SPA static files with index.html fallback.

    Returns:
        Combined ASGI app callable.
    """
    spa_handler = make_spa_handler(ui_dist) if ui_dist is not None else None

    async def combined_app(scope: dict, receive: Any, send: Any) -> None:
        """Combined ASGI app that routes to metrics/health, REST API, or MCP."""
        scope_type = scope["type"]
        if scope_type in ("http", "websocket"):
            path = scope.get("path", "")
            # Health/metrics only available on HTTP (not WebSocket).
            if scope_type == "http" and path in ("/health", "/ready", "/metrics"):
                await aux_app(scope, receive, send)
                return
            if api_app is not None and (path == "/api" or path.startswith("/api/")):
                # Strip /api prefix before forwarding to api_app.
                scope = dict(scope)
                scope["path"] = path[4:] or "/"
                scope["root_path"] = scope.get("root_path", "") + "/api"
                await api_app(scope, receive, send)
                return
            # Serve UI static files for non-API HTTP requests (SPA routing).
            if scope_type == "http" and spa_handler is not None:
                await spa_handler(scope, receive, send)
                return
        await mcp_app(scope, receive, send)

    return combined_app


def create_auth_combined_app(
    aux_app: Starlette,
    mcp_app: Any,
    auth_components: "AuthComponents",
    config: "ServerConfig",
    api_app: Any = None,
    ui_dist: Path | None = None,
) -> Any:
    """Create auth-enabled combined ASGI app.

    This wraps the MCP app with authentication middleware while
    keeping health/metrics endpoints unprotected.

    Args:
        aux_app: Starlette app for health/metrics endpoints.
        mcp_app: FastMCP ASGI app.
        auth_components: Authentication components from bootstrap_auth().
        config: Server configuration with auth settings.
        api_app: Optional REST API ASGI app mounted at /api/.
        ui_dist: Optional path to Vite build output. When set, non-API HTTP
            requests are served as SPA static files with index.html fallback
            (without requiring authentication).

    Returns:
        Combined ASGI app with auth middleware.
    """
    from ..domain.contracts.authentication import AuthRequest
    from ..domain.exceptions import AccessDeniedError, AuthenticationError

    skip_paths = set(config.auth_skip_paths)
    trusted_proxies = config.trusted_proxies
    spa_handler = make_spa_handler(ui_dist) if ui_dist is not None else None

    async def auth_combined_app(scope: dict, receive: Any, send: Any) -> None:
        """Combined ASGI app with authentication for MCP endpoints."""
        scope_type = scope["type"]

        # Lifespan events go directly to mcp_app (no routing needed).
        if scope_type == "lifespan":
            await mcp_app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Skip auth for health/metrics endpoints (HTTP only).
        if scope_type == "http" and path in skip_paths:
            await aux_app(scope, receive, send)
            return

        # Route /api/ paths to REST API for both HTTP and WebSocket (no auth on API).
        if api_app is not None and (path == "/api" or path.startswith("/api/")):
            api_scope = dict(scope)
            api_scope["path"] = path[4:] or "/"
            api_scope["root_path"] = scope.get("root_path", "") + "/api"
            await api_app(api_scope, receive, send)
            return

        # Serve UI static files for HTTP without auth (SPA does its own auth via API).
        if scope_type == "http" and spa_handler is not None:
            await spa_handler(scope, receive, send)
            return

        # For non-API HTTP scopes, apply authentication before forwarding to mcp_app.
        if scope_type != "http":
            # WebSocket on non-/api/ paths goes to mcp_app without auth (MCP protocol).
            await mcp_app(scope, receive, send)
            return

        # Build headers dict from scope (HTTP only from here).
        headers = {}
        for key, value in scope.get("headers", []):
            headers[key.decode("latin-1").lower()] = value.decode("latin-1")

        # Get client IP.
        client = scope.get("client")
        source_ip = client[0] if client else "unknown"

        # Trust X-Forwarded-For only from trusted proxies.
        if source_ip in trusted_proxies:
            forwarded_for = headers.get("x-forwarded-for")
            if forwarded_for:
                source_ip = forwarded_for.split(",")[0].strip()

        # Create auth request.
        auth_request = AuthRequest(
            headers=headers,
            source_ip=source_ip,
            method=scope.get("method", ""),
            path=path,
        )

        try:
            auth_context = auth_components.authn_middleware.authenticate(auth_request)
            scope["auth"] = auth_context
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
    "resolve_ui_dist",
    "make_spa_handler",
]
