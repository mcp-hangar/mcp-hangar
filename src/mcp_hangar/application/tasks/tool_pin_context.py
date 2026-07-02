"""Request-scoped current-tool digest pin for the MCP task lifecycle (#320).

Hangar pins a per-tenant tool digest on the synchronous ``tools/call`` invoke
path (see :mod:`mcp_hangar.server.tools.batch.executor`). When such a call is
task-augmented and returns a task handle, the digest that was authorized at
invoke time MUST be carried into :class:`GovernedTaskStore.create_task` so the
task can be bound to it and re-verified fail-closed when its result is later
retrieved. ``TaskMetadata`` carries only ``ttl`` (no tool info), so the tool
identity and its pinned digest are threaded through this contextvar -- exactly
the pattern Hangar already uses for request identity
(:data:`mcp_hangar.context.identity_context_var`).

The contextvar is set at the invoke-path pin site (where Hangar knows the
tool's pinned digest) and read in ``create_task``. Each batch call runs in its
own :func:`contextvars.copy_context` (see the executor), so the value is
confined to the call that set it and needs no teardown; a token-based reset is
still provided for callers that set it on a shared context.

WARNING: this supports the EXPERIMENTAL mcp task API; the wire format may churn.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class CurrentToolPin:
    """The tool identity and pinned digest authorized for the current request.

    Attributes:
        mcp_server: Owning mcp_server (or group) id the call targets.
        tool_name: Tool name invoked on the call path.
        pinned_digest: 64-char hex SHA-256 the caller's tenant pinned this tool
            to (``ToolDigest.sha256``). This is the digest a resulting task is
            bound to and re-verified against on result retrieval.
    """

    mcp_server: str
    tool_name: str
    pinned_digest: str


_current_tool_pin_var: ContextVar[CurrentToolPin | None] = ContextVar(
    "current_tool_pin",
    default=None,
)


def get_current_tool_pin() -> CurrentToolPin | None:
    """Return the current-tool pin bound to this request, or ``None``."""
    return _current_tool_pin_var.get()


def set_current_tool_pin(pin: CurrentToolPin) -> Token[CurrentToolPin | None]:
    """Bind *pin* as the current-tool pin, returning a reset token."""
    return _current_tool_pin_var.set(pin)


def reset_current_tool_pin(token: Token[CurrentToolPin | None]) -> None:
    """Restore the previous current-tool pin using *token*."""
    _current_tool_pin_var.reset(token)


def clear_current_tool_pin() -> None:
    """Clear any current-tool pin bound to this request."""
    _current_tool_pin_var.set(None)


__all__ = [
    "CurrentToolPin",
    "clear_current_tool_pin",
    "get_current_tool_pin",
    "reset_current_tool_pin",
    "set_current_tool_pin",
]
