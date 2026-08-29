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

from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any
import uuid

import structlog

from mcp_hangar.domain.value_objects.identity import IdentityContext

# Context variables for request-scoped data
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
server_name_var: ContextVar[str | None] = ContextVar("server_name", default=None)
tool_name_var: ContextVar[str | None] = ContextVar("tool_name", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)

identity_context_var: ContextVar[IdentityContext | None] = ContextVar("identity_context", default=None)

#: This request's SEP-2243 routing headers, lower-cased: the ``Mcp-Param-*``
#: values an L7 egress policy may select on, the ``MCP-Protocol-Version`` that
#: says whether they were *owed* a check against the body, and -- under
#: :data:`PARAM_VALIDATION_KEY` -- whether that check actually ran (#1058,
#: ADR-025).
#:
#: The version was only ever a proxy for the second question, and the two part
#: company on a failed pre-dispatch listing: the SDK skips validation, dispatch
#: continues, and a modern request reaches a selector carrying a header nothing
#: compared against the body.
#:
#: Deliberately not the whole header bag. A policy engine that could read
#: ``Authorization`` would be a way to exfiltrate a credential by writing globs
#: until one matched, so only the headers a policy is allowed to see are carried.
routing_headers_var: ContextVar[Mapping[str, str] | None] = ContextVar("routing_headers", default=None)

#: Where the listing path records that ``Mcp-Param-*`` validation was skipped
#: for this POST. The carrier is ``request.state`` and not a contextvar because
#: :func:`bind_routing_headers` rebuilds the mapping from the raw request
#: headers rather than merging into it, so a contextvar set out in the nested
#: listing is overwritten by the later bind (ADR-025). It is the same per-POST
#: channel the projection memo already uses (#1049).
PARAM_VALIDATION_STATE_ATTR = "hangar_param_validation_skipped"

#: The entry :func:`bind_routing_headers` adds to the mapping to carry the skip
#: status to the evaluator. Not an HTTP header: an ``MCPEgressPolicy`` selector
#: can only name an ``Mcp-Param-*`` header (``egress_l7._header_matches``
#: refuses anything else), so no operator rule can match or forge this key, and
#: a client sending it by that name is filtered out below.
PARAM_VALIDATION_KEY = "hangar-param-validation"

#: Values of :data:`PARAM_VALIDATION_KEY`.
PARAM_VALIDATION_RAN = "ran"
PARAM_VALIDATION_SKIPPED = "skipped"


def select_routing_headers(headers: Mapping[str, str] | None) -> Mapping[str, str]:
    """Narrow a request's headers to the ones an egress policy may select on."""
    if not headers:
        return {}
    return {
        key.lower(): value
        for key, value in headers.items()
        if key.lower().startswith("mcp-param-") or key.lower() == "mcp-protocol-version"
    }


def get_routing_headers() -> Mapping[str, str] | None:
    """This request's selectable routing headers, or ``None`` when unbound."""
    return routing_headers_var.get()


def bind_routing_headers(request_context: Any) -> Any:
    """Bind the selectable headers off a per-request context, or return ``None``.

    Same bridge, and the same reason, as ``bind_caller_identity``: the SDK runs
    each inbound message in a task decoupled from the ASGI wrapper, so a
    contextvar set out there is not the one the handler reads. The L7 evaluator
    runs further down still, on a batch worker thread that inherits this via
    ``copy_context()``.

    Duck-typed on purpose -- it accepts a ``ServerRequestContext`` or anything
    exposing one as ``.request_context`` -- so the front door and the batch
    surface share one definition instead of growing one each.

    The mapping also carries whether this request's ``Mcp-Param-*`` headers were
    validated against the body, read off ``request.state`` where the listing
    path left it (ADR-025). Absent state means the ladder ran, which is what a
    request that reaches a handler at all has done unless something recorded
    otherwise.

    Returns a token to reset, or ``None``. Fully fault-barriered.
    """
    try:
        inner = getattr(request_context, "request_context", None) or request_context
        request = getattr(inner, "request", None)
        selected = dict(select_routing_headers(getattr(request, "headers", None)))
        if not selected:
            return None
        skipped = bool(getattr(getattr(request, "state", None), PARAM_VALIDATION_STATE_ATTR, False))
        selected[PARAM_VALIDATION_KEY] = PARAM_VALIDATION_SKIPPED if skipped else PARAM_VALIDATION_RAN
        return routing_headers_var.set(selected)
    except Exception:  # noqa: BLE001 -- header bags vary; a missed bind must never break a call
        return None


def release_routing_headers(token: Any) -> None:
    """Reset what :func:`bind_routing_headers` bound, if anything."""
    if token is None:
        return
    try:
        routing_headers_var.reset(token)
    except Exception:  # noqa: BLE001 -- best-effort cleanup
        pass


def generate_request_id() -> str:
    """Generate a short unique request ID."""
    return uuid.uuid4().hex[:12]


def get_request_id() -> str | None:
    """Get the current request ID from context."""
    return request_id_var.get()


def get_identity_context() -> IdentityContext | None:
    """Get the current identity context."""
    return identity_context_var.get()


def bind_request_context(
    request_id: str | None = None,
    server_name: str | None = None,
    tool_name: str | None = None,
    user_id: str | None = None,
    identity_context: IdentityContext | None = None,
    **extra: Any,
) -> str:
    """Bind contextual information to all logs in the current scope.

    This function sets context variables and binds them to structlog's contextvars,
    ensuring all subsequent log entries include this information.

    Args:
        request_id: Unique identifier for the request. Auto-generated if not provided.
        server_name: Name of the target server/mcp_server.
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
    if identity_context is not None:
        identity_context_var.set(identity_context)

    # Build context dict for structlog
    context: dict[str, Any] = {"request_id": request_id}
    if server_name is not None:
        context["server"] = server_name
    if tool_name is not None:
        context["tool"] = tool_name
    if user_id is not None:
        context["user_id"] = user_id
    if identity_context is not None and identity_context.caller:
        context["caller_agent_id"] = identity_context.caller.agent_id
        context["caller_user_id"] = identity_context.caller.user_id
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
    identity_context_var.set(None)


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
        identity_context: IdentityContext | None = None,
        **extra: Any,
    ):
        self._request_id = request_id
        self._server_name = server_name
        self._tool_name = tool_name
        self._user_id = user_id
        self._identity_context = identity_context
        self._extra = extra
        self.request_id: str | None = None

    def __enter__(self) -> RequestContextManager:
        self.request_id = bind_request_context(
            request_id=self._request_id,
            server_name=self._server_name,
            tool_name=self._tool_name,
            user_id=self._user_id,
            identity_context=self._identity_context,
            **self._extra,
        )
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        clear_request_context()

    async def __aenter__(self) -> RequestContextManager:
        return self.__enter__()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.__exit__(exc_type, exc_val, exc_tb)
