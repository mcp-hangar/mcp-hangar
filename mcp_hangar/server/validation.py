"""Validation and error handling for MCP tools.

This module provides validation functions that use the ApplicationContext
for accessing rate limiter and security handler, following DIP.
"""

from ..application.mcp.tooling import ToolErrorPayload
from ..domain.exceptions import RateLimitExceeded
from ..domain.security.input_validator import (
    validate_arguments,
    validate_provider_id,
    validate_timeout,
    validate_tool_name,
)
from .context import get_context


def check_rate_limit(key: str = "global") -> None:
    """Check rate limit and raise exception if exceeded.

    Gets rate limiter from application context (DIP).
    """
    ctx = get_context()
    result = ctx.rate_limiter.consume(key)
    if not result.allowed:
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
        context: Additional context dict with provider_id, tool, etc.
    """
    try:
        ctx = get_context()
        ctx.security_handler.log_validation_failed(
            field="tool",
            message=f"{type(exc).__name__}: {str(exc) or 'unknown error'}",
            provider_id=context.get("provider_id"),
            value=context.get("provider_id"),
        )
    except (RuntimeError, AttributeError, TypeError):
        # Context not initialized or handler missing - skip silently
        pass


def validate_provider_id_input(provider: str) -> None:
    """Validate provider ID and raise exception if invalid."""
    result = validate_provider_id(provider)
    if not result.valid:
        ctx = get_context()
        ctx.security_handler.log_validation_failed(
            field="provider",
            message=(result.errors[0].message if result.errors else "Invalid provider ID"),
            provider_id=provider,
        )
        raise ValueError(f"invalid_provider_id: {result.errors[0].message if result.errors else 'validation failed'}")


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
