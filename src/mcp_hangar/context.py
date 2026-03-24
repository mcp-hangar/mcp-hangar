"""Request context management using contextvars.

This module provides utilities for binding contextual information to log entries
within a request scope. All logs emitted during request processing will automatically
include the bound context (request_id, server_name, tool_name, etc.).

Usage:
    from mcp_hangar.context import bind_request_context, clear_request_context

    async def handle_request(request):
        bind_request_context(
            request_id=request.id,
            server_name="filesystem",
            tool_name="read_file",
        )
        try:
            # All logs in this scope will include the bound context
            logger.info("processing_request")
            result = await process(request)
            logger.info("request_completed")
            return result
        finally:
            clear_request_context()
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any
import uuid

import structlog

# Context variables for request-scoped data
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
server_name_var: ContextVar[str | None] = ContextVar("server_name", default=None)
tool_name_var: ContextVar[str | None] = ContextVar("tool_name", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


def generate_request_id() -> str:
    """Generate a short unique request ID."""
    return uuid.uuid4().hex[:12]


def get_request_id() -> str | None:
    """Get the current request ID from context."""
    return request_id_var.get()


def bind_request_context(
    request_id: str | None = None,
    server_name: str | None = None,
    tool_name: str | None = None,
    user_id: str | None = None,
    **extra: Any,
) -> str:
    """Bind contextual information to all logs in the current scope.

    This function sets context variables and binds them to structlog's contextvars,
    ensuring all subsequent log entries include this information.

    Args:
        request_id: Unique identifier for the request. Auto-generated if not provided.
        server_name: Name of the target server/provider.
        tool_name: Name of the tool being invoked.
        user_id: Optional user identifier for attribution.
        **extra: Additional key-value pairs to include in log context.

    Returns:
        The request_id (either provided or generated).

    Example:
        request_id = bind_request_context(
            server_name="filesystem",
            tool_name="read_file",
            path="/tmp/test.txt",
        )
    """
    # Generate request_id if not provided
    if request_id is None:
        request_id = generate_request_id()

    # Set context variables
    request_id_var.set(request_id)
    if server_name is not None:
        server_name_var.set(server_name)
    if tool_name is not None:
        tool_name_var.set(tool_name)
    if user_id is not None:
        user_id_var.set(user_id)

    # Build context dict for structlog
    context: dict[str, Any] = {"request_id": request_id}
    if server_name is not None:
        context["server"] = server_name
    if tool_name is not None:
        context["tool"] = tool_name
    if user_id is not None:
        context["user_id"] = user_id
    context.update(extra)

    # Clear any previous context and bind new one
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(**context)

    return request_id


def update_request_context(**kwargs: Any) -> None:
    """Update the current request context with additional information.

    This is useful for adding information that becomes available during processing,
    such as the routed server name or response status.

    Args:
        **kwargs: Key-value pairs to add to the context.

    Example:
        update_request_context(routed_to="memory-server", status="success")
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    """Clear all request-scoped context.

    Should be called at the end of request processing to prevent context leakage.
    """
    structlog.contextvars.clear_contextvars()
    request_id_var.set(None)
    server_name_var.set(None)
    tool_name_var.set(None)
    user_id_var.set(None)


class RequestContextManager:
    """Context manager for automatic request context handling.

    Example:
        async with RequestContextManager(tool_name="read_file") as ctx:
            logger.info("processing")
            # ctx.request_id is available
    """

    def __init__(
        self,
        request_id: str | None = None,
        server_name: str | None = None,
        tool_name: str | None = None,
        user_id: str | None = None,
        **extra: Any,
    ):
        self._request_id = request_id
        self._server_name = server_name
        self._tool_name = tool_name
        self._user_id = user_id
        self._extra = extra
        self.request_id: str | None = None

    def __enter__(self) -> RequestContextManager:
        self.request_id = bind_request_context(
            request_id=self._request_id,
            server_name=self._server_name,
            tool_name=self._tool_name,
            user_id=self._user_id,
            **self._extra,
        )
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        clear_request_context()

    async def __aenter__(self) -> RequestContextManager:
        return self.__enter__()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.__exit__(exc_type, exc_val, exc_tb)
