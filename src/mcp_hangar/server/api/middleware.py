# pyright: reportAny=false, reportExplicitAny=false, reportUnusedParameter=false

"""API middleware for auth, error handling, CORS, and CQRS dispatch helpers.

Provides:
- AuthEnforcementMiddleware: shared HTTP/WebSocket auth enforcement
- create_auth_enforced_app: reusable auth wrapper for arbitrary ASGI apps
- error_handler: Converts domain exceptions to JSON error envelopes
- dispatch_query: Async wrapper for query bus calls via run_in_threadpool
- dispatch_command: Async wrapper for command bus calls via run_in_threadpool
- get_cors_config: Validated CORS configuration from environment
"""

import logging
import os
import re
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlparse

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from ...domain.contracts.authentication import AuthRequest
from ...domain.exceptions import (
    AccessDeniedError,
    AuthenticationError,
    AuthorizationError,
    MCPError,
    McpServerDegradedError,
    McpServerNotFoundError,
    McpServerNotReadyError,
    RateLimitExceeded,
    RateLimitExceededError,
    ToolNotFoundError,
    ToolTimeoutError,
    ValidationError,
)
from ...infrastructure.identity.trusted_proxy import TrustedProxyResolver, headers_from_asgi_scope, resolve_source_ip
from ..context import get_context
from .serializers import HangarJSONResponse

logger = logging.getLogger(__name__)

_ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_ALLOWED_HEADERS = [
    "Authorization",
    "Content-Type",
    "X-API-Key",
    "X-Correlation-ID",
    "X-Requested-With",
]
_ORIGIN_RE = re.compile(r"^https?://[^*\s]+$")
_CSRF_PROTECTED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CSRF_BYPASS_AUTH_SCHEME = "bearer "
_BROWSER_HINT_HEADERS = ("origin", "referer", "cookie")
_SESSION_SUSPEND_PATH_RE = re.compile(r"^/sessions/(?P<session_id>[^/]+)/suspend/?$")
_VALID_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_DEFAULT_AUTH_SKIP_PATHS = frozenset({"/health/live", "/health/ready", "/health/startup", "/metrics"})


class _AuthLoggerAdapter:
    @staticmethod
    def warning(event: str, **kwargs: Any) -> None:
        logger.warning("%s %s", event, kwargs)

# Mapping of exception types to HTTP status codes.
# More specific types must come before their base classes.
_EXCEPTION_STATUS_MAP: list[tuple[type, int]] = [
    (McpServerNotFoundError, 404),
    (ToolNotFoundError, 404),
    (McpServerNotReadyError, 409),
    (ValidationError, 422),
    (RateLimitExceededError, 429),
    (RateLimitExceeded, 429),
    (AuthenticationError, 401),
    (AccessDeniedError, 403),
    (AuthorizationError, 403),
    (McpServerDegradedError, 503),
    (ToolTimeoutError, 504),
    (MCPError, 500),
]


class CSRFMiddleware(BaseHTTPMiddleware):
    """Require X-Requested-With on mutating browser-style requests."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        method = request.method.upper()
        headers = Headers(scope=request.scope)
        if (
            method not in _CSRF_PROTECTED_METHODS
            or not self._is_browser_csrf_path(request.url.path)
            or not self._is_browser_request(headers)
            or self._should_skip_csrf(headers)
            or headers.get("x-requested-with", "").strip()
        ):
            return await call_next(request)

        return HangarJSONResponse(
            {
                "error": "csrf_header_required",
                "message": "X-Requested-With header is required for mutating requests",
            },
            status_code=403,
        )

    @staticmethod
    def _should_skip_csrf(headers: Headers) -> bool:
        if headers.get("x-api-key", "").strip():
            return True

        authorization = headers.get("authorization", "")
        return authorization.lower().startswith(_CSRF_BYPASS_AUTH_SCHEME)

    @staticmethod
    def _is_browser_request(headers: Headers) -> bool:
        return any(headers.get(header, "").strip() for header in _BROWSER_HINT_HEADERS)

    @staticmethod
    def _is_browser_csrf_path(path: str) -> bool:
        match = _SESSION_SUSPEND_PATH_RE.match(path)
        if match is None:
            return False

        return _VALID_SESSION_ID_RE.match(match.group("session_id")) is not None


def _should_skip_auth_path(path: str, skip_paths: frozenset[str]) -> bool:
    return path in skip_paths or path.startswith("/health/")


def _headers_from_scope(scope: Scope) -> dict[str, str]:
    headers = headers_from_asgi_scope(scope.get("headers"))
    if scope["type"] == "websocket":
        raw_query_string = scope.get("query_string", b"")
        query_params = parse_qs(raw_query_string.decode("latin-1"))
        token = query_params.get("token", [None])[0]
        if token and "authorization" not in headers and "x-api-key" not in headers:
            headers["authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    return headers


def _build_auth_request(scope: Scope, trusted_proxies: TrustedProxyResolver) -> tuple[AuthRequest, str]:
    headers = _headers_from_scope(scope)
    client = scope.get("client")
    client_host = client[0] if client else None
    source_ip = (
        resolve_source_ip(headers=headers, client_host=client_host, trusted_proxies=trusted_proxies)
        or "unknown"
    )
    method = scope.get("method", "GET" if scope["type"] == "websocket" else "")
    path = scope.get("path", "")
    return AuthRequest(headers=headers, source_ip=source_ip, method=method, path=path), source_ip


def _store_auth_context(scope: Scope, auth_context: Any) -> None:
    scope["auth"] = auth_context
    state = scope.setdefault("state", {})
    if isinstance(state, dict):
        state["auth"] = auth_context


async def _send_auth_failure(
    scope: Scope,
    receive: Receive,
    send: Send,
    exc: AuthenticationError | AccessDeniedError,
    source_ip: str,
) -> None:
    path = scope.get("path", "")
    if scope["type"] == "websocket":
        event_name = "ws_authentication_failed" if isinstance(exc, AuthenticationError) else "ws_access_denied"
        message = exc.message if isinstance(exc, AuthenticationError) else str(exc)
        _AuthLoggerAdapter.warning(event_name, path=path, source_ip=source_ip, message=message)
        await send({"type": "websocket.close", "code": 1008, "reason": message})
        return

    if isinstance(exc, AuthenticationError):
        response = JSONResponse(
            status_code=401,
            content={
                "error": "authentication_failed",
                "message": exc.message,
                "details": exc.details,
            },
            headers={"WWW-Authenticate": "Bearer, ApiKey"},
        )
    else:
        response = JSONResponse(
            status_code=403,
            content={
                "error": "access_denied",
                "message": str(exc),
                "principal_id": exc.principal_id,
                "action": exc.action,
                "resource": exc.resource,
            },
        )

    await response(scope, receive, send)


class AuthEnforcementMiddleware:
    """Shared HTTP/WebSocket auth enforcement middleware."""

    app: ASGIApp
    _authn: Any
    _skip_paths: frozenset[str]
    _trusted_proxies: TrustedProxyResolver

    def __init__(
        self,
        app: ASGIApp,
        authn: Any,
        skip_paths: frozenset[str] | None = None,
        trusted_proxies: TrustedProxyResolver | None = None,
    ) -> None:
        self.app = app
        self._authn = authn
        self._skip_paths = skip_paths or _DEFAULT_AUTH_SKIP_PATHS
        self._trusted_proxies = trusted_proxies or TrustedProxyResolver()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if _should_skip_auth_path(path, self._skip_paths):
            await self.app(scope, receive, send)
            return

        auth_request, source_ip = _build_auth_request(scope, self._trusted_proxies)
        try:
            auth_context = self._authn.authenticate(auth_request)
            _store_auth_context(scope, auth_context)
            await self.app(scope, receive, send)
        except (AuthenticationError, AccessDeniedError) as exc:
            await _send_auth_failure(scope, receive, send, exc, source_ip)


class AuthMiddlewareHTTP(BaseHTTPMiddleware):
    """Starlette HTTP middleware adapter over shared auth enforcement."""

    _authn: Any
    _skip_paths: frozenset[str]
    _trusted_proxies: TrustedProxyResolver

    def __init__(self, app: ASGIApp, authn: Any, skip_paths: frozenset[str] | None = None) -> None:
        super().__init__(app)
        self._authn = authn
        self._skip_paths = skip_paths or _DEFAULT_AUTH_SKIP_PATHS
        self._trusted_proxies = TrustedProxyResolver()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if _should_skip_auth_path(path, self._skip_paths):
            return await call_next(request)

        auth_request, _source_ip = _build_auth_request(request.scope, self._trusted_proxies)
        try:
            auth_context = self._authn.authenticate(auth_request)
            request.state.auth = auth_context
            request.scope["auth"] = auth_context
            return await call_next(request)
        except AuthenticationError as exc:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "authentication_failed",
                    "message": exc.message,
                    "details": exc.details,
                },
                headers={"WWW-Authenticate": "Bearer, ApiKey"},
            )
        except AccessDeniedError as exc:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "access_denied",
                    "message": str(exc),
                    "principal_id": exc.principal_id,
                    "action": exc.action,
                    "resource": exc.resource,
                },
            )


def create_auth_enforced_app(
    inner_app: ASGIApp,
    auth_components: Any,
    *,
    skip_paths: frozenset[str] | None = None,
) -> ASGIApp:
    """Wrap an ASGI app with shared auth enforcement when available."""
    authn = getattr(auth_components, "authn_middleware", None)
    if authn is None:
        return inner_app
    return AuthEnforcementMiddleware(inner_app, authn=authn, skip_paths=skip_paths)


def _get_status_code(exc: Exception) -> int:
    """Determine HTTP status code for an exception.

    Args:
        exc: The exception to map.

    Returns:
        HTTP status code integer.
    """
    for exc_type, status_code in _EXCEPTION_STATUS_MAP:
        if isinstance(exc, exc_type):
            return status_code
    return 500


async def error_handler(request: Request, exc: Exception) -> HangarJSONResponse:
    """Convert exceptions to JSON error envelopes.

    Maps domain exceptions to appropriate HTTP status codes.
    Unhandled exceptions get 500 with a generic message (internals not leaked).

    Args:
        request: The Starlette request.
        exc: The exception that was raised.

    Returns:
        HangarJSONResponse with error envelope body.
    """
    status_code = _get_status_code(exc)

    if isinstance(exc, MCPError):
        error_body: dict[str, Any] = {
            "error": {
                "code": type(exc).__name__,
                "message": exc.message,
                "details": exc.details or None,
            }
        }
    else:
        # Generic exception -- do NOT expose internal message
        logger.exception("Unhandled exception in API request", exc_info=exc)
        error_body = {
            "error": {
                "code": "InternalServerError",
                "message": "An internal server error occurred.",
                "details": None,
            }
        }

    return HangarJSONResponse(error_body, status_code=status_code)


async def dispatch_query(query: Any) -> Any:
    """Dispatch a query to the query bus using run_in_threadpool.

    The backend is thread-based, so all CQRS calls must be executed
    via run_in_threadpool to avoid blocking the async event loop.

    Args:
        query: The query to execute.

    Returns:
        Result from query_bus.execute(query).
    """
    ctx = get_context()
    return await run_in_threadpool(ctx.query_bus.execute, query)


async def dispatch_command(command: Any) -> Any:
    """Dispatch a command to the command bus using run_in_threadpool.

    The backend is thread-based, so all CQRS calls must be executed
    via run_in_threadpool to avoid blocking the async event loop.

    Args:
        command: The command to send.

    Returns:
        Result from command_bus.send(command).
    """
    ctx = get_context()
    return await run_in_threadpool(ctx.command_bus.send, command)


def _validate_origin(origin: str) -> str | None:
    """Return the origin if valid, or None otherwise.

    Logs a warning on rejection.
    """
    origin = origin.strip()
    if not _ORIGIN_RE.match(origin):
        logger.warning("cors_origin_rejected origin=%s reason=%s", origin, "invalid format or wildcard")
        return None
    parsed = urlparse(origin)
    if not parsed.hostname:
        logger.warning("cors_origin_rejected origin=%s reason=%s", origin, "no hostname")
        return None
    return origin


def get_cors_config() -> dict[str, Any]:
    """Get CORS configuration from environment variables.

    Reads MCP_CORS_ORIGINS (comma-separated) from the environment.
    Each origin must have a scheme (http:// or https://) and no wildcards.
    Defaults to http://localhost:5173 when MCP_CORS_ORIGINS is not set.

    allow_credentials is False by default. Set MCP_CORS_CREDENTIALS=true
    to enable (requires explicit non-wildcard origins).

    Returns:
        Dict of CORSMiddleware kwargs.
    """
    cors_origins_env = os.environ.get("MCP_CORS_ORIGINS", "")
    if cors_origins_env.strip():
        raw = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
        allow_origins = [o for o in (_validate_origin(o) for o in raw) if o is not None]
        if not allow_origins:
            logger.warning("cors_all_origins_rejected fallback=%s", "http://localhost:5173")
            allow_origins = ["http://localhost:5173"]
    else:
        allow_origins = ["http://localhost:5173"]

    allow_credentials = os.environ.get("MCP_CORS_CREDENTIALS", "false").lower() == "true"

    return {
        "allow_origins": allow_origins,
        "allow_methods": _ALLOWED_METHODS,
        "allow_headers": _ALLOWED_HEADERS,
        "allow_credentials": allow_credentials,
    }
