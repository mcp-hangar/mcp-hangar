"""One bounded span per governed task follow-up, linked to the call that created the task (#1281).

A relayed task outlives the request that created it, so a follow-up is never
parented on that request (ADR-029 s2). Each served ``tasks/get``,
``tasks/cancel`` and ``tasks/update`` opens one ``task_relay.<op>`` span under
the request's SDK SERVER span, which is that request's entry (ADR-029 s3), and
adds one link to the ``batch.call.<tool>`` span recorded on the ledger entry
(ADR-029 s8). It never opens a second root for the request (ADR-029,
Alternative 2).

The link, and the entry's server and tool, are added only once the caller is
shown to own the task: a foreign task yields neither. An entry with no origin,
or a task this process does not hold -- restarted, or registered on another
replica -- gives no link. Nothing is invented.

Hangar's own cancel of a task nobody was handed runs on a thread with no
request, so it opens ``task_relay.cancel_unhanded`` in a new trace linked to
the refused call, as a timer-fired command does.

What each span carries is bounded: ``hangar.task.outcome`` from a closed list,
``mcp.server.id``, ``gen_ai.tool.name`` and, on a refusal or failure, a bounded
``error.type``. Never the task id: it is minted upstream and unbounded, and the
link already joins a follow-up to its call. Status follows ADR-029 s5: a
refusal and ``not_found`` stay UNSET, a failed relay ends ERROR. The raised
``McpError`` is not an ``ExpectedRefusal``, so it is caught here, settled, and
re-raised outside the span rather than left for the tracer to mark ERROR.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from ...errors import bounded_error_type
from ...logging_config import get_logger
from ...observability.conventions import GenAI, McpServer, TaskRelay
from ...observability.tracing import (
    ERROR_TYPE,
    get_tracer,
    link_span_to_origin,
    mark_span_error,
    new_trace_linked_to,
)

logger = get_logger(__name__)

#: Span names: ``task_relay.get``, ``task_relay.cancel``, ``task_relay.update``.
SPAN_PREFIX = "task_relay."
UNHANDED_CANCEL_SPAN = "task_relay.cancel_unhanded"


class _FollowUp:
    """What one follow-up has recorded so far; settled onto its span when it ends."""

    __slots__ = ("error_type", "failed", "outcome", "span")

    def __init__(self, span: Any) -> None:
        self.span = span
        self.outcome: str | None = None
        self.error_type: str | None = None
        self.failed = False


_current: ContextVar[_FollowUp | None] = ContextVar("task_follow_up", default=None)


def _mcp_error_type(error: BaseException) -> str | None:
    """A JSON-RPC error's bounded type: its ``data.error_type`` when it names one, else its code."""
    detail = getattr(error, "error", None)
    code = getattr(detail, "code", None)
    if code is None:
        return None
    data = getattr(detail, "data", None)
    named = data.get("error_type") if isinstance(data, dict) else None
    return named if isinstance(named, str) else str(code)


def _settle(follow_up: _FollowUp, error: BaseException | None) -> None:
    """Write the outcome onto the span. A raise nobody classified is a refusal if it is a JSON-RPC error."""
    try:
        if error is not None and follow_up.outcome is None:
            rpc_type = _mcp_error_type(error)
            if rpc_type is not None:
                follow_up.outcome, follow_up.error_type = TaskRelay.REFUSED, rpc_type
            else:
                follow_up.outcome, follow_up.failed = TaskRelay.ERROR, True
                follow_up.error_type = type(error).__qualname__
        span = follow_up.span
        if follow_up.outcome in TaskRelay.OUTCOMES:
            span.set_attribute(TaskRelay.OUTCOME, follow_up.outcome)
        if follow_up.failed:
            mark_span_error(span, follow_up.error_type or "_OTHER")
        elif follow_up.error_type is not None:
            span.set_attribute(ERROR_TYPE, bounded_error_type(follow_up.error_type))
    except Exception:  # noqa: BLE001 -- fault barrier: telemetry must not break a follow-up
        logger.debug("task_follow_up_settle_failed")


@contextmanager
def _follow_up_span(name: str, **start: Any) -> Iterator[_FollowUp]:
    raised: Exception | None = None
    with get_tracer(__name__).start_as_current_span(name, **start) as span:
        follow_up = _FollowUp(span)
        token = _current.set(follow_up)
        try:
            yield follow_up
        except Exception as error:  # noqa: BLE001 -- settled here, re-raised below, outside the span
            raised = error
        finally:
            _current.reset(token)
            _settle(follow_up, raised)
    if raised is not None:
        raise raised


def traced_follow_up(op: str, handler: Callable[[Any, Any], Awaitable[Any]]) -> Callable[[Any, Any], Awaitable[Any]]:
    """``handler`` inside one ``task_relay.<op>`` span, a child of the request's SERVER span."""

    async def traced(ctx: Any, params: Any) -> Any:
        with _follow_up_span(SPAN_PREFIX + op):
            return await handler(ctx, params)

    return traced


def follow_up_authorized(origin: str, mcp_server_id: str | None, tool_name: str | None) -> None:
    """The caller owns the task: link to its origin and name its server and tool. Never raises.

    Call only after ownership and ``authorize`` both succeed.
    """
    follow_up = _current.get()
    if follow_up is None:
        return
    try:
        link_span_to_origin(follow_up.span, origin)
        if mcp_server_id:
            follow_up.span.set_attribute(McpServer.ID, mcp_server_id)
        if tool_name:
            follow_up.span.set_attribute(GenAI.TOOL_NAME, tool_name)
    except Exception:  # noqa: BLE001 -- fault barrier: telemetry must not break a follow-up
        logger.debug("task_follow_up_link_failed")


def record_follow_up(outcome: str, *, error_type: str | None = None, failed: bool = False) -> None:
    """Record how the current follow-up ended. ``failed`` ends it ERROR; ``error_type`` is bounded on write."""
    follow_up = _current.get()
    if follow_up is not None:
        follow_up.outcome, follow_up.error_type, follow_up.failed = outcome, error_type, failed


def relay_error_type(answer: Any) -> str | None:
    """A raw upstream answer's JSON-RPC error code, ``_OTHER`` for an unreadable one, None when it is no error."""
    if not isinstance(answer, dict) or "error" not in answer:
        return None
    code = answer["error"].get("code") if isinstance(answer["error"], dict) else None
    return str(code) if type(code) is int else "_OTHER"


@contextmanager
def unhanded_cancel_span(origin: str | None, mcp_server_id: str, tool_name: str) -> Iterator[None]:
    """``task_relay.cancel_unhanded``: a new root linked to the refused call, as a timer-fired command is."""
    with _follow_up_span(UNHANDED_CANCEL_SPAN, **new_trace_linked_to(origin)) as follow_up:
        try:
            if mcp_server_id:
                follow_up.span.set_attribute(McpServer.ID, mcp_server_id)
            if tool_name:
                follow_up.span.set_attribute(GenAI.TOOL_NAME, tool_name)
        except Exception:  # noqa: BLE001 -- fault barrier: telemetry must not break a cancel
            logger.debug("task_unhanded_cancel_attributes_failed")
        yield
