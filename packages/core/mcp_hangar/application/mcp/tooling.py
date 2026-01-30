"""MCP tool wiring utilities.

This module provides a decorator for MCP tool functions to standardize:
- rate limiting
- input validation
- consistent error mapping
- structured security logging hooks

It is intentionally framework-agnostic: it does not import FastMCP directly.
The decorator is meant to be applied to functions already registered via
`@mcp.tool(...)` in `registry/server.py`.

Design notes:
- The decorator takes callables for rate limiting, validation, and error mapping.
- It keeps the wrapped function signature compatible with MCP tool calling.
- Supports both sync and async tool functions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeVar

from ...logging_config import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class ToolErrorPayload:
    """Normalized error payload returned to MCP client.

    MCP tools often return structured output; we keep this minimal and stable.
    """

    error: str
    error_type: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.error,
            "type": self.error_type,
            "details": self.details,
        }


def _default_error_mapper(exc: Exception) -> ToolErrorPayload:
    """Fallback error mapper."""
    return ToolErrorPayload(
        error=str(exc) or "unknown error",
        error_type=type(exc).__name__,
        details={},
    )


def mcp_tool_wrapper(
    *,
    tool_name: str,
    rate_limit_key: Callable[..., str],
    check_rate_limit: Callable[[str], None],
    validate: Callable[..., None] | None = None,
    error_mapper: Callable[[Exception], ToolErrorPayload] | None = None,
    on_error: Callable[[Exception, dict[str, Any]], None] | None = None,
) -> Callable[[F], F]:
    """Decorator to standardize MCP tool behavior.

    Args:
        tool_name: Human-readable tool name (used in error payload metadata).
        rate_limit_key: Callable that builds a rate limit bucket key from args/kwargs.
        check_rate_limit: Callable that enforces rate limit for the computed key.
                          Should raise (e.g. RateLimitExceeded) when exceeded.
        validate: Optional callable to validate inputs. Should raise ValueError on invalid input.
                  Signature should match the wrapped tool function.
        error_mapper: Optional callable mapping Exception -> ToolErrorPayload.
                      If omitted, a minimal default is used.
        on_error: Optional hook called on exception with (exc, context_dict).

    Returns:
        Decorated function.
    """
    mapper = error_mapper or _default_error_mapper

    def decorator(func: F) -> F:
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:

            @wraps(func)
            async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
                # Rate limit first (cheapest check) to reduce abuse surface.
                key = rate_limit_key(*args, **kwargs)
                check_rate_limit(key)

                # Validate inputs if provided.
                if validate is not None:
                    validate(*args, **kwargs)

                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    # Optional error hook (e.g. security auditing).
                    if on_error is not None:
                        try:
                            on_error(
                                exc,
                                {
                                    "tool": tool_name,
                                    "rate_limit_key": key,
                                    "args_count": len(args),
                                    "kwargs_keys": list(kwargs.keys()),
                                },
                            )
                        except (TypeError, ValueError, RuntimeError) as hook_err:
                            logger.debug(
                                "error_hook_failed",
                                tool=tool_name,
                                hook_error=str(hook_err),
                            )

                    payload = mapper(exc)
                    return payload.to_dict()

            return async_wrapped  # type: ignore[return-value]
        else:

            @wraps(func)
            def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
                # Rate limit first (cheapest check) to reduce abuse surface.
                key = rate_limit_key(*args, **kwargs)
                check_rate_limit(key)

                # Validate inputs if provided.
                if validate is not None:
                    validate(*args, **kwargs)

                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    # Optional error hook (e.g. security auditing).
                    if on_error is not None:
                        try:
                            on_error(
                                exc,
                                {
                                    "tool": tool_name,
                                    "rate_limit_key": key,
                                    "args_count": len(args),
                                    "kwargs_keys": list(kwargs.keys()),
                                },
                            )
                        except (TypeError, ValueError, RuntimeError) as hook_err:
                            logger.debug(
                                "error_hook_failed",
                                tool=tool_name,
                                hook_error=str(hook_err),
                            )

                    payload = mapper(exc)
                    return payload.to_dict()

            return sync_wrapped  # type: ignore[return-value]

    return decorator


def key_global(*_: Any, **__: Any) -> str:
    """Rate limit key for globally-scoped tools."""
    return "global"


def key_per_provider(provider: str, *_: Any, **__: Any) -> str:
    """Rate limit key scoped per provider."""
    return f"provider:{provider}"


def key_hangar_call(provider: str, tool: str, *_: Any, **__: Any) -> str:
    """Rate limit key specialized for tool invocation (per provider)."""
    # Keep it coarse by default to avoid key explosion; include tool name if desired.
    return f"hangar_call:{provider}"


def chain_validators(*validators: Callable[..., None]) -> Callable[..., None]:
    """Combine multiple validators into a single callable.

    Each validator is called in order. First exception stops the chain.
    """

    def _combined(*args: Any, **kwargs: Any) -> None:
        for v in validators:
            v(*args, **kwargs)

    return _combined
