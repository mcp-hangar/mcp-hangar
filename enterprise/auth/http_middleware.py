"""Enterprise HTTP authentication adapter.

This middleware is the HTTP-facing auth enforcement path for enterprise API
requests. It reuses the core ``mcp_hangar.infrastructure.identity`` request
normalization helpers to extract headers/source IP consistently with
``IdentityMiddleware``, then performs authentication and stores the resulting
``AuthContext`` on ``request.state`` for downstream authorization checks.
"""

# pyright: reportImplicitOverride=false

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from mcp_hangar.domain.contracts.authentication import AuthRequest
from mcp_hangar.domain.exceptions import AccessDeniedError, AuthenticationError
from mcp_hangar.domain.value_objects import Principal
from mcp_hangar.infrastructure.identity import TrustedProxyResolver, normalize_http_headers, resolve_source_ip
from mcp_hangar.logging_config import get_logger
from enterprise.auth.infrastructure.middleware import AuthContext, AuthenticationMiddleware

logger = get_logger(__name__)


class AuthMiddlewareHTTP(BaseHTTPMiddleware):
    """Starlette middleware for enterprise HTTP authentication.

    This middleware shares request metadata extraction with
    ``mcp_hangar.infrastructure.identity.middleware.IdentityMiddleware`` so
    there is a single normalization path. It then adds enterprise-specific
    authentication enforcement and attaches ``request.state.auth`` for
    downstream authorization.

    Skips authentication for configured paths (health, metrics, etc.).

    Usage:
        from starlette.applications import Starlette
        from mcp_hangar.server.http_auth_middleware import AuthMiddlewareHTTP

        app = Starlette()
        app.add_middleware(AuthMiddlewareHTTP, authn=authn_middleware)

        # In route handler:
        @app.route("/providers")
        async def list_providers(request):
            auth_context = request.state.auth  # AuthContext
            principal = auth_context.principal
    """

    _authn: AuthenticationMiddleware
    _skip_paths: list[str]
    _trusted_proxies: TrustedProxyResolver

    def __init__(
        self,
        app: ASGIApp,
        authn: AuthenticationMiddleware,
        skip_paths: list[str] | None = None,
    ) -> None:
        """Initialize the HTTP auth middleware.

        Args:
            app: The ASGI application.
            authn: Authentication middleware to use.
            skip_paths: Paths to skip authentication (e.g., ["/health", "/metrics"]).
        """
        super().__init__(app)
        self._authn = authn
        self._skip_paths = skip_paths or ["/health", "/ready", "/_ready", "/metrics"]
        self._trusted_proxies = TrustedProxyResolver()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request through authentication middleware.

        Args:
            request: The incoming Starlette request.
            call_next: The next middleware/handler in the chain.

        Returns:
            Response from the handler or error response.
        """
        # Skip auth for certain paths
        if request.url.path in self._skip_paths:
            return await call_next(request)

        # Build auth request from HTTP request
        auth_request = self._build_auth_request(request)

        try:
            # Authenticate
            auth_context = self._authn.authenticate(auth_request)
            request.state.auth = auth_context
            return await call_next(request)

        except AuthenticationError as e:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "authentication_failed",
                    "message": e.message,
                    "details": e.details,
                },
                headers={"WWW-Authenticate": "Bearer, ApiKey"},
            )

        except AccessDeniedError as e:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "access_denied",
                    "message": str(e),
                    "principal_id": e.principal_id,
                    "action": e.action,
                    "resource": e.resource,
                },
            )

    def _build_auth_request(self, request: Request) -> AuthRequest:
        """Build AuthRequest from Starlette Request.

        Args:
            request: The Starlette request.

        Returns:
            AuthRequest for the authentication middleware.
        """
        headers = normalize_http_headers(request.headers)
        source_ip = (
            resolve_source_ip(
                headers=headers,
                client_host=request.client.host if request.client else None,
                trusted_proxies=self._trusted_proxies,
            )
            or "unknown"
        )

        return AuthRequest(
            headers=headers,
            source_ip=source_ip,
            method=request.method,
            path=request.url.path,
        )


def get_principal_from_request(request: Request) -> Principal | None:
    """Get authenticated principal from request.

    Helper function to extract principal from request state.

    Args:
        request: The Starlette request.

    Returns:
        Principal from auth context, or None if not authenticated.
    """
    auth_context = getattr(request.state, "auth", None)
    if auth_context:
        return auth_context.principal if isinstance(auth_context, AuthContext) else None
    return None


def require_auth(request: Request) -> Principal:
    """Require authentication for a request.

    Helper function that raises if request is not authenticated.

    Args:
        request: The Starlette request.

    Returns:
        Principal if authenticated.

    Raises:
        AuthenticationError: If not authenticated.
    """
    from mcp_hangar.domain.exceptions import MissingCredentialsError

    principal = get_principal_from_request(request)
    if principal is None or principal.is_anonymous():
        raise MissingCredentialsError("Authentication required")
    return principal
