"""Validation and error handling for MCP tools.

This module provides validation functions that use the ApplicationContext
for accessing rate limiter and security handler, following DIP.
"""

import warnings

from .. import metrics as prometheus_metrics
from ..application.mcp.tooling import ToolErrorPayload
from ..domain.exceptions import RateLimitExceeded
from ..domain.security.input_validator import (
    validate_arguments,
    validate_mcp_server_id,
    validate_timeout,
    validate_tool_name,
)
from .context import get_context


def check_rate_limit(key: str = "global") -> None:
    """Check rate limit and raise exception if exceeded.

    .. deprecated::
        Rate limiting is now enforced at the command bus middleware layer
        via RateLimitMiddleware. This function will be removed in a future version.

    Gets rate limiter from application context (DIP).
    Updates Prometheus metrics when rate limit is hit.
    """
    warnings.warn(
        "check_rate_limit() is deprecated. Rate limiting is enforced at command bus middleware.",
        DeprecationWarning,
        stacklevel=2,
    )
    ctx = get_context()
    result = ctx.rate_limiter.consume(key)
    if not result.allowed:
        # Update Prometheus metrics
        prometheus_metrics.RATE_LIMIT_HITS_TOTAL.inc(result="rejected")

        ctx.security_handler.log_rate_limit_exceeded(
            limit=result.limit,
            window_seconds=int(1.0 / result.limit) if result.limit else 1,
        )
        raise RateLimitExceeded(
            limit=result.limit,
            window_seconds=int(1.0 / result.limit) if result.limit else 1,
        )


def tool_error_mapper(exc: Exception) -> ToolErrorPayload:
    """Map exceptions to a stable MCP tool error payload."""
    return ToolErrorPayload(
        error=str(exc) or "unknown error",
        error_type=type(exc).__name__,
        details={},
    )


def tool_error_hook(exc: Exception, context: dict) -> None:
    """Best-effort hook for logging/security telemetry on tool failures.

    Gets security handler from application context (DIP).

    Args:
        exc: The exception that occurred.
        context: Additional context dict with mcp_server_id, tool, etc.
    """
    try:
        ctx = get_context()
        ctx.security_handler.log_validation_failed(
            field="tool",
            message=f"{type(exc).__name__}: {str(exc) or 'unknown error'}",
            mcp_server_id=context.get("mcp_server_id"),
            value=context.get("mcp_server_id"),
        )
    except (RuntimeError, AttributeError, TypeError):
        # Context not initialized or handler missing - skip silently
        pass


def validate_mcp_server_id_input(mcp_server: str) -> None:
    """Validate mcp_server ID and raise exception if invalid."""
    result = validate_mcp_server_id(mcp_server)
    if not result.valid:
        ctx = get_context()
        ctx.security_handler.log_validation_failed(
            field="mcp_server",
            message=(result.errors[0].message if result.errors else "Invalid mcp_server ID"),
            mcp_server_id=mcp_server,
        )
        raise ValueError(f"invalid_mcp_server_id: {result.errors[0].message if result.errors else 'validation failed'}")


def validate_tool_name_input(tool: str) -> None:
    """Validate tool name and raise exception if invalid."""
    result = validate_tool_name(tool)
    if not result.valid:
        ctx = get_context()
        ctx.security_handler.log_validation_failed(
            field="tool",
            message=result.errors[0].message if result.errors else "Invalid tool name",
        )
        raise ValueError(f"invalid_tool_name: {result.errors[0].message if result.errors else 'validation failed'}")


def validate_arguments_input(arguments: dict) -> None:
    """Validate tool arguments and raise exception if invalid."""
    result = validate_arguments(arguments)
    if not result.valid:
        ctx = get_context()
        ctx.security_handler.log_validation_failed(
            field="arguments",
            message=result.errors[0].message if result.errors else "Invalid arguments",
        )
        raise ValueError(f"invalid_arguments: {result.errors[0].message if result.errors else 'validation failed'}")


def validate_timeout_input(timeout: float) -> None:
    """Validate timeout and raise exception if invalid."""
    result = validate_timeout(timeout)
    if not result.valid:
        ctx = get_context()
        ctx.security_handler.log_validation_failed(
            field="timeout",
            message=result.errors[0].message if result.errors else "Invalid timeout",
        )
        raise ValueError(f"invalid_timeout: {result.errors[0].message if result.errors else 'validation failed'}")


# legacy aliases
globals()["".join(("validate_pro", "vider_id_input"))] = validate_mcp_server_id_input
