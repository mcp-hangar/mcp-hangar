"""One structured line per call on the front door's projected surface (#1362).

The front door logged nothing per flat ``tools/call`` that said who called what
and what happened. A client's deterministic failure, reported with its own
request id, had nothing server-side to be matched against: no tool, no caller,
no verdict. A call that reaches the executor does publish ``BatchCallCompleted``
(a ``domain_event`` line naming the tool), but that line carries no caller and no
request id, and a call refused before the executor -- a ``-32601``, a suspended
session, a refused header -- publishes nothing at all. Those are the denials, and
a deny that leaves no trace is the case this module exists for.

So every call through the flat handler writes exactly one ``front_door_tool_call``
line, whatever happened to it. ``outcome`` is one of six values:

``ok``
    Served, and the result is not an error.
``tool_error``
    The caller got ``isError`` and it was not a refusal: the upstream failed or
    answered with an error, or the call could not be completed (timeout, cold
    start, open circuit). ``reason`` is the executor's error type when it had one.
``denied``
    An enforcement refusal: access policy, withdrawal, digest pin, approval,
    validator, egress policy, or a suspended session. ``reason`` is its code.
``not_found``
    ``-32601``. ``reason`` is ``not_projected`` when the name is an upstream tool
    this gateway holds but does not project to this caller -- the shape a policy
    deny takes on this surface (#905) -- and ``unknown`` when no upstream holds it.
``rejected``
    Any other JSON-RPC error the handler raised; ``header_mismatch`` for the
    ADR-025 refusal, ``jsonrpc_<code>`` otherwise.
``error``
    An unexpected exception; ``reason`` is its class name.

The other fields are ``tool`` (the name the caller asked for), ``principal_id``,
``tenant_id``, ``request_id`` (the JSON-RPC id) and ``duration_ms``. When tracing
is on the log pipeline adds ``trace_id`` and ``span_id``, from the server span the
SDK opens around every handler.

**No arguments are logged**: not raw, not redacted, not counted. The line exists
to correlate a call, and request id, caller and tool are what correlate it.
Nothing in the arguments helps with that, and every value in them is a potential
secret that redaction would have to recognise. One line per call with no
per-argument expansion is also what bounds the hot path. The two strings the
caller chose, the tool name and a string request id, are cut to
:data:`CALLER_TEXT_LIMIT`.

A suspended session's refusal is the one line that does not name the tool: it
carries neither the tool name nor the request id. An operator has cut that
caller off, and nothing it chose is echoed into the log -- the rule the session
guard's own ``session_suspended_call_refused`` line already follows
(GHSA-fhwh-fmq2-7m5c). Its principal, tenant and verdict are still there.

Nothing here changes what the caller receives: the handler's result or exception
passes through untouched. The ``not_projected``/``unknown`` split exists only in
the operator's log. The caller gets the same ``-32601`` either way, and telling
the two apart does the same work in both cases, a set built from the whole
catalogue.

INFO, the level a ``hangar_call`` call reaches the log at (``domain_event`` for
``BatchCallCompleted``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
import functools
import time
from typing import Any

from mcp_hangar._sdk_compat import METHOD_NOT_FOUND, McpError

from ..application.read_models.tool_projection import get_tool_projection_registry
from ..context import get_identity_context
from ..logging_config import get_logger, truncate_text
from ..tasks_wire import HEADER_MISMATCH

logger = get_logger(__name__)

CALL_LOG_EVENT = "front_door_tool_call"

OUTCOME_OK = "ok"
OUTCOME_TOOL_ERROR = "tool_error"
OUTCOME_DENIED = "denied"
OUTCOME_NOT_FOUND = "not_found"
OUTCOME_REJECTED = "rejected"
OUTCOME_ERROR = "error"

NOT_PROJECTED = "not_projected"
UNKNOWN_NAME = "unknown"
HEADER_MISMATCH_REASON = "header_mismatch"

#: Codes a refused call carries: this gateway deciding, not an upstream failing.
#: The session guard's reasons and the executor's enforcement gates. Any
#: other code a failed call carries is a ``tool_error``, so a refusal missing here
#: is still logged, with its code as the ``reason``. Drift mislabels a line; it
#: never loses one.
#: The session guard's two reasons (``server.session_guard``, not imported: the
#: server package imports this module back).
SESSION_REFUSALS = frozenset({"session_suspended", "session_suspension_unchecked"})

DENIAL_CODES = SESSION_REFUSALS | frozenset(
    {
        "ToolAccessDeniedError",
        "ToolAccessDenied",
        "ToolWithdrawnError",
        "ToolDigestMismatchError",
        "ValidatorDenied",
        "EgressPolicyDeniedError",
        "EgressPolicyApprovalRequiredError",
        "ApprovalDenied",
        "approval_denied",
        "approval_timeout",
        "ApprovalGateError",
        "ApprovalNoLongerValid",
        "ApprovalRevalidationError",
    }
)

#: The longest value kept of a string the caller chose.
CALLER_TEXT_LIMIT = 128


@dataclass
class _CallNote:
    """What the handler knew about a failed call that its result no longer says."""

    code: str | None = None


_call_note: ContextVar[_CallNote | None] = ContextVar("front_door_call_note", default=None)


def note_failure(code: str | None) -> None:
    """Record why the call being handled failed, for its log line.

    The handler turns a refusal into an ``isError`` result that carries only
    text, so the code is gone by the time the result reaches the log. A no-op
    outside a logged call.
    """
    note = _call_note.get()
    if note is not None:
        note.code = code or "UnknownError"


def _is_error_result(result: Any) -> bool:
    if isinstance(result, dict):
        return result.get("isError") is True
    return bool(getattr(result, "is_error", False) or getattr(result, "isError", False))


def _outcome_of_result(result: Any, note: _CallNote) -> tuple[str, str | None]:
    if note.code is not None:
        return (OUTCOME_DENIED if note.code in DENIAL_CODES else OUTCOME_TOOL_ERROR), note.code
    return (OUTCOME_TOOL_ERROR if _is_error_result(result) else OUTCOME_OK), None


def _why_not_found(name: str) -> str:
    held = {projection.tool for projection in get_tool_projection_registry().all()}
    return NOT_PROJECTED if name in held else UNKNOWN_NAME


def _outcome_of_exception(exc: BaseException, name: str) -> tuple[str, str | None]:
    if not isinstance(exc, McpError):
        return OUTCOME_ERROR, type(exc).__name__
    code = exc.error.code
    if code == METHOD_NOT_FOUND:
        return OUTCOME_NOT_FOUND, _why_not_found(name)
    if code == HEADER_MISMATCH:
        return OUTCOME_REJECTED, HEADER_MISMATCH_REASON
    return OUTCOME_REJECTED, f"jsonrpc_{code}"


def _caller_text(value: Any) -> Any:
    """*value* bounded: an int as it is, anything else as a string cut to the limit."""
    if value is None or isinstance(value, int):
        return value
    return truncate_text(str(value), CALLER_TEXT_LIMIT)


def _log_call(name: Any, mcp_ctx: Any, started: float, classify: Callable[[], tuple[str, str | None]]) -> None:
    try:
        outcome, reason = classify()
        echo = reason not in SESSION_REFUSALS  # nothing a suspended caller chose is echoed
        identity = get_identity_context()
        caller = identity.caller if identity is not None else None
        logger.info(
            CALL_LOG_EVENT,
            tool=_caller_text(name) if echo else None,
            outcome=outcome,
            reason=reason,
            principal_id=(caller.user_id or caller.agent_id) if caller is not None else None,
            tenant_id=caller.tenant_id if caller is not None else None,
            request_id=_caller_text(getattr(mcp_ctx, "request_id", None)) if echo else None,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    except Exception:  # noqa: BLE001 -- fault-barrier: the line must never fail the call it describes
        pass


def logging_each_call(call_tool: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Write one ``front_door_tool_call`` line for every call through *call_tool*.

    Meant to be the outermost decorator on the flat handler, so a call refused
    before the handler body (a suspended session) is logged like any other. The
    result or exception is passed through untouched.
    """

    @functools.wraps(call_tool)
    async def logged(name: str, arguments: dict[str, Any], mcp_ctx: Any = None) -> Any:
        note = _CallNote()
        token = _call_note.set(note)
        started = time.perf_counter()
        try:
            result = await call_tool(name, arguments, mcp_ctx)
        except BaseException as exc:
            _log_call(name, mcp_ctx, started, functools.partial(_outcome_of_exception, exc, name))
            raise
        finally:
            _call_note.reset(token)
        _log_call(name, mcp_ctx, started, functools.partial(_outcome_of_result, result, note))
        return result

    return logged
