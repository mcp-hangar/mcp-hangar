"""ASGI application factory and authentication middleware.

Provides functions to create ASGI applications with health endpoints
and optional authentication middleware.
"""

from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from ..context import identity_context_var
from ..domain.value_objects.identity import CallerIdentity, IdentityContext
from ..domain.value_objects.security import PrincipalType
from ..logging_config import get_logger

if TYPE_CHECKING:
    from typing import Any as AuthComponents
    from .config import ServerConfig

logger = get_logger(__name__)


def _principal_to_identity_context(principal: Any) -> IdentityContext:
    """Bridge an authenticated Principal to an IdentityContext for identity_context_var.

    Mapping rules:
    - PrincipalType.USER        → principal_type "user",    user_id = principal.id.value
    - PrincipalType.SERVICE_ACCOUNT → principal_type "service", user_id = principal.id.value
    - PrincipalType.SYSTEM      → principal_type "service"  (system is a non-human identity;
                                  closest valid literal is "service"), user_id = principal.id.value
    - Anonymous (id == "anonymous") → principal_type "anonymous", user_id = None
    - tenant_id passes through from Principal.tenant_id.

    CallerIdentity.__post_init__ requires user_id non-None for "user"/"service".
    We fall back to "anonymous" only if principal_type would require a user_id but
    the id is somehow empty (defensive — should not happen in practice).
    """
    if principal is None or principal.is_anonymous():
        return IdentityContext(
            caller=CallerIdentity(
                user_id=None,
                agent_id=None,
                session_id=None,
                principal_type="anonymous",
                tenant_id=None,
            )
        )

    principal_id_value: str = principal.id.value
    p_type = principal.type  # PrincipalType enum

    if p_type == PrincipalType.USER:
        mapped_type: str = "user"
    else:
        # SERVICE_ACCOUNT and SYSTEM both map to "service"
        mapped_type = "service"

    # CallerIdentity requires user_id non-None for "user"/"service".
    # Guard: if somehow the id is empty, fall back to anonymous rather than crashing.
    if not principal_id_value:
        return IdentityContext(
            caller=CallerIdentity(
                user_id=None,
                agent_id=None,
                session_id=None,
                principal_type="anonymous",
                tenant_id=principal.tenant_id,
            )
        )

    return IdentityContext(
        caller=CallerIdentity(
            user_id=principal_id_value,
            agent_id=None,
            session_id=None,
            principal_type=mapped_type,  # type: ignore[arg-type]
            tenant_id=principal.tenant_id,
        )
    )


def create_health_routes(
    run_readiness_checks: Callable[[], dict[str, Any]],
    update_metrics: Callable[[], None],
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


def create_combined_asgi_app(
    aux_app: Starlette,
    mcp_app: Any,
    api_app: Any = None,
) -> Any:
    """Create combined ASGI app that routes to metrics/health, REST API, or MCP.

    Args:
        aux_app: Starlette app for health/metrics endpoints.
        mcp_app: FastMCP ASGI app.
        api_app: Optional REST API ASGI app mounted at /api/.

    Returns:
        Combined ASGI app callable.
    """

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
        await mcp_app(scope, receive, send)

    return combined_app


def _strip_host_port(host: str) -> str:
    """Return the hostname from a Host header, dropping the port.

    Handles ``host:port``, bracketed IPv6 ``[::1]:port``, and bare hostnames /
    IPv4 / bracketless IPv6 (left unchanged).
    """
    host = host.strip()
    if host.startswith("["):  # [::1]:8000 -> ::1
        return host[1:].split("]", 1)[0]
    if host.count(":") == 1:  # host:port -> host
        return host.rsplit(":", 1)[0]
    return host


def _ws_handshake_allowed(scope: dict) -> tuple[bool, str]:
    """Validate a non-``/api/`` WebSocket handshake at the Hangar edge.

    Defense-in-depth against DNS rebinding / cross-origin WebSocket abuse
    (CVE-2026-59950 class): the SDK terminates the MCP protocol, but Hangar is
    the trust boundary and should not rely solely on the SDK for origin checks.

    Posture (see #498):

    - **Loopback connections are trusted** (local, no browser same-origin or
      rebinding threat) and always pass.
    - On **non-loopback** connections (fail-closed):
      - **Origin** is browser-scoped -- a *present* Origin must be in the
        allowlist (``MCP_CORS_ORIGINS``); a *missing* Origin is a non-browser
        client (no same-origin policy to bypass) and is allowed, auth still
        applies.
      - **Host** must be in the trusted-hosts allowlist (``MCP_TRUSTED_HOSTS`` --
        the same list the REST API's TrustedHostMiddleware uses). ``*`` disables
        the check.

    Returns ``(allowed, reason)``; ``reason`` is a short tag for logging.
    """
    import os

    from ..server.api.middleware import get_cors_config
    from ..server.lifecycle import _is_loopback_host

    server = scope.get("server")
    if server and _is_loopback_host(str(server[0])):
        return True, ""

    headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
    origin = headers.get("origin")
    host = headers.get("host", "")

    if origin is not None:
        allowed_origins = set(get_cors_config()["allow_origins"])
        if "*" not in allowed_origins and origin not in allowed_origins:
            return False, f"origin_not_allowed:{origin}"

    trusted_env = os.environ.get("MCP_TRUSTED_HOSTS", "localhost,127.0.0.1,::1,testserver")
    trusted_hosts = [h.strip() for h in trusted_env.split(",") if h.strip()]
    if "*" not in trusted_hosts and _strip_host_port(host) not in trusted_hosts:
        return False, f"host_not_allowed:{host or '<missing>'}"

    return True, ""


def create_auth_combined_app(
    aux_app: Starlette,
    mcp_app: Any,
    auth_components: "AuthComponents",
    config: "ServerConfig",
    api_app: Any = None,
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

    Returns:
        Combined ASGI app with auth middleware.
    """
    from ..auth.prm import build_resource_base_url, build_www_authenticate
    from ..domain.contracts.authentication import AuthRequest
    from ..domain.exceptions import AccessDeniedError, AuthenticationError
    from ..server.api.middleware import _store_auth_context

    _oidc_issuer = getattr(auth_components, "oidc_issuer", "")
    _oidc_resource_uri_cfg = getattr(auth_components, "oidc_resource_uri", "")

    skip_paths = set(config.auth_skip_paths)
    trusted_proxies = config.trusted_proxies

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

        # Non-HTTP (WebSocket) on non-/api/ paths. The MCP protocol carries no auth
        # here, but Hangar is the trust boundary and validates the handshake
        # Origin/Host before forwarding (DNS-rebinding / cross-origin defense; #498).
        if scope_type != "http":
            allowed, reason = _ws_handshake_allowed(scope)
            if not allowed:
                logger.warning("ws_handshake_rejected", reason=reason, path=path)
                await receive()  # drain the websocket.connect event
                await send({"type": "websocket.close", "code": 1008})
                return
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
            _store_auth_context(scope, auth_context)
            # Bridge: propagate the authenticated Principal into identity_context_var
            # so the batch executor and per-tenant enforcement (#229/#236/#231) can
            # read caller.tenant_id for this request.
            identity_ctx = _principal_to_identity_context(getattr(auth_context, "principal", None))
            token = identity_context_var.set(identity_ctx)
            try:
                await mcp_app(scope, receive, send)
            finally:
                identity_context_var.reset(token)

        except AuthenticationError as e:
            # RFC 9728: include resource_metadata in Bearer challenge when OIDC is active.
            if _oidc_issuer:
                resource_base = _oidc_resource_uri_cfg or build_resource_base_url(scope)
                www_auth = build_www_authenticate(resource_base)
            else:
                www_auth = "Bearer, ApiKey"
            response = JSONResponse(
                status_code=401,
                content={
                    "error": "authentication_failed",
                    "message": e.message,
                },
                headers={"WWW-Authenticate": www_auth},
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
