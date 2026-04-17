# pyright: reportAny=false, reportExplicitAny=false, reportUnusedParameter=false

"""API middleware for error handling, CORS, and CQRS dispatch helpers.

Provides:
- error_handler: Converts domain exceptions to JSON error envelopes
- dispatch_query: Async wrapper for query bus calls via run_in_threadpool
- dispatch_command: Async wrapper for command bus calls via run_in_threadpool
- get_cors_config: Validated CORS configuration from environment
"""

import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from ...domain.exceptions import (
    AccessDeniedError,
    AuthenticationError,
    AuthorizationError,
    MCPError,
    ProviderDegradedError,
    ProviderNotFoundError,
    ProviderNotReadyError,
    RateLimitExceeded,
    RateLimitExceededError,
    ToolNotFoundError,
    ToolTimeoutError,
    ValidationError,
)
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

# Mapping of exception types to HTTP status codes.
# More specific types must come before their base classes.
_EXCEPTION_STATUS_MAP: list[tuple[type, int]] = [
    (ProviderNotFoundError, 404),
    (ToolNotFoundError, 404),
    (ProviderNotReadyError, 409),
    (ValidationError, 422),
    (RateLimitExceededError, 429),
    (RateLimitExceeded, 429),
    (AuthenticationError, 401),
    (AccessDeniedError, 403),
    (AuthorizationError, 403),
    (ProviderDegradedError, 503),
    (ToolTimeoutError, 504),
    (MCPError, 500),
]


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
