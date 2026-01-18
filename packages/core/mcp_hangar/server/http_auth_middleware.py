"""Starlette/FastAPI middleware for authentication.

Integrates auth middleware with HTTP frameworks.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..domain.contracts.authentication import AuthRequest
from ..domain.exceptions import AccessDeniedError, AuthenticationError
from ..infrastructure.auth.middleware import AuthenticationMiddleware


class AuthMiddlewareHTTP(BaseHTTPMiddleware):
    """Starlette middleware for HTTP authentication.

    Authenticates incoming requests and attaches auth context to request.state.
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

    def __init__(
        self,
        app,
        authn: AuthenticationMiddleware,
        skip_paths: list[str] | None = None,
    ):
        """Initialize the HTTP auth middleware.

        Args:
            app: The ASGI application.
            authn: Authentication middleware to use.
            skip_paths: Paths to skip authentication (e.g., ["/health", "/metrics"]).
        """
        super().__init__(app)
        self._authn = authn
        self._skip_paths = skip_paths or ["/health", "/ready", "/_ready", "/metrics"]

    async def dispatch(self, request: Request, call_next):
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
        # Get client IP from socket
        source_ip = "unknown"
        if request.client:
            source_ip = request.client.host

        # Only trust X-Forwarded-For if request comes from a trusted proxy
        # This prevents IP spoofing attacks
        if source_ip in self._trusted_proxies:
            forwarded_for = request.headers.get("x-forwarded-for")
            if forwarded_for:
                # Take the first IP in the chain (original client)
                # Note: In a chain of proxies, you may need to take a different position
                source_ip = forwarded_for.split(",")[0].strip()

        return AuthRequest(
            headers=dict(request.headers),
            source_ip=source_ip,
            method=request.method,
            path=request.url.path,
        )


def get_principal_from_request(request: Request):
    """Get authenticated principal from request.

    Helper function to extract principal from request state.

    Args:
        request: The Starlette request.

    Returns:
        Principal from auth context, or None if not authenticated.
    """
    auth_context = getattr(request.state, "auth", None)
    if auth_context:
        return auth_context.principal
    return None


def require_auth(request: Request):
    """Require authentication for a request.

    Helper function that raises if request is not authenticated.

    Args:
        request: The Starlette request.

    Returns:
        Principal if authenticated.

    Raises:
        AuthenticationError: If not authenticated.
    """
    from ..domain.exceptions import MissingCredentialsError

    principal = get_principal_from_request(request)
    if principal is None or principal.is_anonymous():
        raise MissingCredentialsError("Authentication required")
    return principal
