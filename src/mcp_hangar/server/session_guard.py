"""Refusing a call from a suspended session (GHSA-fhwh-fmq2-7m5c).

``POST /api/sessions/{id}/suspend`` recorded a suspension and replicated it to
every replica, and nothing on any request path read it back: a suspended
session went on invoking tools. This is the one reader. Every invoke chokepoint
calls :func:`refuse_if_session_suspended` before it does anything for the
caller -- before validation, authorization, approval, cold start or upstream
I/O -- so the chokepoints cannot disagree about what "suspended" means:

* ``hangar_call``, at its entry;
* the front door's flat ``tools/call``, at its entry;
* every other ``hangar_*`` tool, including both continuation tools, through
  ``tool_permissions.authorize_tool``, which ``mcp_tool_wrapper`` runs for each;
* ``tasks/get``, ``tasks/cancel`` and ``tasks/update``, at the entry of each
  relay handler (``tasks/result`` and ``tasks/list`` are not served);
* on a front door, every other method that reaches an upstream on the caller's
  behalf: ``prompts/list``, ``prompts/get``, ``completion/complete``,
  ``resources/list``, ``resources/templates/list``, ``resources/read`` and
  ``subscriptions/listen``. These refuse through
  :func:`refuse_request_if_session_suspended`, as a JSON-RPC error.

``tools/list`` is not refused: it answers from Hangar's own catalogue and
reaches no upstream.

What it can and cannot refuse
-----------------------------
A suspension names a session id, so it refuses only a caller that carries that
id: the ``sid`` claim of a verified bearer token, or an ``x-session-id`` header
from a trusted proxy (``fastmcp_server.asgi._caller_session_id``). A caller with
neither carries no session id, and there is nothing to match. It is not refused.
That is a limit of what a session id is, not a gap this module could close:
suspending a *principal* is a different control.

Replicated suspensions are refused too. The registry read here is the one
``SessionSuspensionProjection`` applies peers' decisions to, so a session
suspended on another replica is refused once this replica has tailed the event.
"""

from __future__ import annotations

from typing import Any, Literal

from ..context import get_identity_context
from ..domain.contracts.session_suspension import ISessionSuspensionRegistry, is_well_formed_session_id
from ..logging_config import get_logger

logger = get_logger(__name__)

#: Where a refusal happened. A fixed vocabulary, so the log line carries no text
#: the caller chose.
Chokepoint = Literal[
    "hangar_call",
    "flat_tool",
    "management_tool",
    "task_relay",
    "prompt",
    "completion",
    "resource",
    "subscription",
]

SESSION_SUSPENDED_MESSAGE = "Session suspended: this call was refused."
SUSPENSION_UNCHECKED_MESSAGE = "Session suspension could not be checked: this call was refused."

#: The JSON-RPC error a method without a tool-error shape refuses with.
#: ``INVALID_REQUEST``, the code the SDK reports for a terminated session; the
#: ``data.reason`` is the machine-readable part, as with
#: ``protocol.SESSION_TERMINATED_REASON``, and callers should key on it.
SESSION_SUSPENDED_CODE = -32600
SESSION_SUSPENDED_REASON = "session_suspended"
SUSPENSION_UNCHECKED_REASON = "session_suspension_unchecked"


class SessionSuspendedError(PermissionError):
    """The caller's session is suspended, or whether it is could not be told.

    ``PermissionError``, like ``ToolAccessNotAuthorizedError``, so it is not
    swallowed by the value-error handling in tool bodies. The message is one of
    two fixed strings and never names the session or the tool.
    """

    def __init__(self, message: str = SESSION_SUSPENDED_MESSAGE) -> None:
        super().__init__(message)
        self.reason = SESSION_SUSPENDED_REASON if message == SESSION_SUSPENDED_MESSAGE else SUSPENSION_UNCHECKED_REASON


def _registry() -> ISessionSuspensionRegistry:
    """The process registry: the one the routes write and the projection applies to."""
    from .api.sessions import get_session_suspension_registry

    return get_session_suspension_registry()


def _caller_session_id(request_ctx: Any) -> str | None:
    """The bound identity's session id, or the request's when none is bound yet.

    ``hangar_call`` binds the identity only after its own gates, so at its entry
    the identity is resolved from the request the same way the bridge will.

    A request that cannot be read is no request, exactly as the bridge treats
    it: a context outside a request raises on access (stdio does this), and
    refusing on that would refuse every stdio call. It costs nothing: a caller
    whose request cannot be read is also unattributed, and with auth on the
    authorization gate refuses an unattributed caller.
    """
    identity = get_identity_context()
    if identity is None and request_ctx is not None:
        from ..fastmcp_server.asgi import identity_for_request

        try:
            identity = identity_for_request(request_ctx)
        except Exception:  # noqa: BLE001 -- an unreadable request carries no session id
            identity = None
    return identity.caller.session_id if identity is not None else None


def refuse_if_session_suspended(chokepoint: Chokepoint, request_ctx: Any = None) -> None:
    """Raise :class:`SessionSuspendedError` if this caller's session is suspended.

    Fail-closed: if the caller carries a session id and the registry cannot
    answer for it, the call is refused rather than served unchecked. A caller
    with no session id is never refused -- see the module docstring for why
    that is the limit.

    Args:
        chokepoint: Which invoke path is asking, for the log line.
        request_ctx: The request context, consulted only when no identity is
            bound yet.

    Raises:
        SessionSuspendedError: If the call must be refused.
    """
    try:
        session_id = _caller_session_id(request_ctx)
        suspended = session_id is not None and _registry().is_suspended(session_id)
    except Exception as exc:  # noqa: BLE001 -- fail-closed: an unreadable suspension refuses
        logger.warning("session_suspension_check_failed", chokepoint=chokepoint, error_type=type(exc).__name__)
        raise SessionSuspendedError(SUSPENSION_UNCHECKED_MESSAGE) from None

    if not suspended:
        return

    # The id matched a suspension, so it is an id the operator named; logged
    # only in the shape the suspend route accepts, and bounded by it.
    logger.warning(
        "session_suspended_call_refused",
        chokepoint=chokepoint,
        session_id=session_id if is_well_formed_session_id(session_id) else None,
    )
    raise SessionSuspendedError()


def refuse_request_if_session_suspended(chokepoint: Chokepoint, request_ctx: Any = None) -> None:
    """:func:`refuse_if_session_suspended`, raised as a JSON-RPC error.

    For the lowlevel handlers that answer a method with no tool-error shape --
    ``tasks/*``, prompts, completions, resources, subscriptions. The decision is
    the one above; only the envelope differs: ``SESSION_SUSPENDED_CODE`` with
    the same fixed message and ``data.reason`` naming the condition.

    Raises:
        McpError: If the call must be refused.
    """
    from .._sdk_compat import make_mcp_error

    try:
        refuse_if_session_suspended(chokepoint, request_ctx)
    except SessionSuspendedError as exc:
        raise make_mcp_error(SESSION_SUSPENDED_CODE, str(exc), {"reason": exc.reason}) from None
