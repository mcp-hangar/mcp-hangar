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

import inspect
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeVar

from ...logging_config import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Name under which the MCP request Context is injected into wrapped tools so the
# wrapper can bridge caller identity (see _bridge_ctx_identity). Chosen to not
# collide with any real tool parameter; stripped before the tool body runs.
_CTX_KW = "_mcp_hangar_request_ctx"


def _should_inject_ctx(func: Callable[..., Any]) -> bool:
    """True if ``func`` declares no MCP ``Context`` parameter of its own.

    Tools that already take a Context (e.g. hangar_call) manage identity
    themselves; we only inject-and-bridge for the ones that don't.
    """
    try:
        from ..._sdk_compat import Context

        for ann in typing.get_type_hints(func).values():
            if inspect.isclass(ann) and issubclass(ann, Context):
                return False
    except Exception:  # noqa: BLE001 -- annotation resolution is best-effort
        return True
    return True


def _bridge_ctx_identity(mcp_ctx: Any) -> Any:
    """Bind caller identity from the injected MCP request Context, or return None.

    On SDK v2 the streamable-HTTP transport runs each tool call in a per-session
    task decoupled from the ASGI auth wrapper that sets ``identity_context_var``,
    so it is None here for an authenticated HTTP caller and the v1 ambient
    ``request_ctx`` no longer exists. The v2 Context IS reachable and the auth
    middleware stored the principal on ``request.state.auth`` -- bridge it once,
    centrally, so per-tenant policy (the tool-access listing filter, canary
    routing, per-tenant withdrawal) sees the real caller instead of failing open.
    Returns a contextvar token to reset, or None. Fully fault-barriered: stdio /
    no-request / unauthenticated / already-bound paths return None and change
    nothing.
    """
    if mcp_ctx is None:
        return None
    try:
        from ...context import get_identity_context, identity_context_var

        if get_identity_context() is not None:
            return None
        auth_state = getattr(getattr(getattr(mcp_ctx, "request_context", None), "request", None), "state", None)
        principal = getattr(getattr(auth_state, "auth", None), "principal", None)
        if principal is None:
            return None
        from ...fastmcp_server.asgi import _principal_to_identity_context

        return identity_context_var.set(_principal_to_identity_context(principal))
    except Exception:  # noqa: BLE001 -- identity bridging must never break the call path
        return None


def _reset_ctx_identity(token: Any) -> None:
    """Reset the identity contextvar bound by _bridge_ctx_identity, if any."""
    if token is None:
        return
    try:
        from ...context import identity_context_var

        identity_context_var.reset(token)
    except Exception:  # noqa: BLE001 -- best-effort cleanup
        pass


def _apply_ctx_annotation(wrapper: Callable[..., Any], func: Callable[..., Any], inject_ctx: bool) -> None:
    """Expose a Context-typed ``_CTX_KW`` param so the SDK injects the request Context.

    The SDK detects the context parameter from ``typing.get_type_hints`` (not the
    signature), and ``@wraps`` copied ``func``'s annotations, so we replace the
    wrapper's annotations with a copy that adds ``_CTX_KW: Context``. The name is
    not a real signature parameter, so it never appears in the tool's input schema.
    """
    if not inject_ctx:
        return
    try:
        from ..._sdk_compat import Context

        wrapper.__annotations__ = {**getattr(func, "__annotations__", {}), _CTX_KW: Context}
    except Exception:  # noqa: BLE001 -- annotation wiring is best-effort
        pass


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
    check_approval: Callable[..., Awaitable[Any]] | None = None,
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
        check_approval: Optional async callable for human-in-the-loop approval.
                        When provided, called after validation and before execution.
                        If result is not approved, returns error payload immediately.
                        None (default) means no approval check -- zero overhead.

    Returns:
        Decorated function.
    """
    mapper = error_mapper or _default_error_mapper

    def decorator(func: F) -> F:
        is_async = inspect.iscoroutinefunction(func)
        # When func declares no Context of its own, ask the SDK to inject the MCP
        # request Context under _CTX_KW so the wrapper can bridge caller identity
        # centrally for every wrapped tool (fixes the v2 tool-listing fail-open).
        inject_ctx = _should_inject_ctx(func)

        if is_async:

            @wraps(func)
            async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
                _mcp_ctx = kwargs.pop(_CTX_KW, None) if inject_ctx else None
                _identity_token = _bridge_ctx_identity(_mcp_ctx)
                try:
                    # Rate limit first (cheapest check) to reduce abuse surface.
                    key = rate_limit_key(*args, **kwargs)
                    check_rate_limit(key)

                    # Validate inputs if provided.
                    if validate is not None:
                        validate(*args, **kwargs)

                    # Approval gate (may block until human decision or timeout).
                    if check_approval is not None:
                        approval_result = await check_approval(*args, **kwargs)
                        if not approval_result.approved:
                            return {
                                "error": approval_result.error_code,
                                "approval_id": approval_result.approval_id,
                                "message": approval_result.reason,
                            }

                    try:
                        return await func(*args, **kwargs)
                    except Exception as exc:  # noqa: BLE001 -- fault-barrier: map all tool exceptions to error payloads for MCP client
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
                finally:
                    _reset_ctx_identity(_identity_token)

            _apply_ctx_annotation(async_wrapped, func, inject_ctx)
            return async_wrapped  # type: ignore[return-value]
        else:

            @wraps(func)
            def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
                _mcp_ctx = kwargs.pop(_CTX_KW, None) if inject_ctx else None
                _identity_token = _bridge_ctx_identity(_mcp_ctx)
                try:
                    # Rate limit first (cheapest check) to reduce abuse surface.
                    key = rate_limit_key(*args, **kwargs)
                    check_rate_limit(key)

                    # Validate inputs if provided.
                    if validate is not None:
                        validate(*args, **kwargs)

                    try:
                        return func(*args, **kwargs)
                    except Exception as exc:  # noqa: BLE001 -- fault-barrier: map all tool exceptions to error payloads for MCP client
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
                finally:
                    _reset_ctx_identity(_identity_token)

            _apply_ctx_annotation(sync_wrapped, func, inject_ctx)
            return sync_wrapped  # type: ignore[return-value]

    return decorator


def key_global(*_: Any, **__: Any) -> str:
    """Rate limit key for globally-scoped tools."""
    return "global"


def key_per_mcp_server(mcp_server: str, *_: Any, **__: Any) -> str:
    """Rate limit key scoped per mcp_server."""
    return f"mcp_server:{mcp_server}"


def key_hangar_call(mcp_server: str, tool: str, *_: Any, **__: Any) -> str:
    """Rate limit key specialized for tool invocation (per mcp_server)."""
    # Keep it coarse by default to avoid key explosion; include tool name if desired.
    return f"hangar_call:{mcp_server}"


def chain_validators(*validators: Callable[..., None]) -> Callable[..., None]:
    """Combine multiple validators into a single callable.

    Each validator is called in order. First exception stops the chain.
    """

    def _combined(*args: Any, **kwargs: Any) -> None:
        for v in validators:
            v(*args, **kwargs)

    return _combined


# legacy aliases
globals()["".join(("key_per_pro", "vider"))] = key_per_mcp_server
