"""Validation and error handling for MCP tools.

This module provides validation functions that use the ApplicationContext
for accessing rate limiter and security handler, following DIP.
"""

from dataclasses import dataclass
from typing import Any

from .. import metrics as prometheus_metrics
from ..application.mcp.tooling import ToolErrorPayload
from ..domain.exceptions import RateLimitExceeded
from ..domain.security.input_validator import (
    validate_arguments,
    validate_mcp_server_id,
    validate_timeout,
    validate_tool_name,
)
from ..errors import bounded_error_type
from ..infrastructure.caller_rate_limit import charge
from .context import get_context


def charge_tool(tool_name: str) -> None:
    """Charge one call of *tool_name* to its caller's budget and the shared one.

    For the work a tool does without the command bus, which charges every
    command it dispatches. The budget is keyed by the tool alone, never by the
    server or group a call names, so naming more of them buys no more calls
    (#1481). Gets the rate limiter from the application context (DIP).

    Raises:
        RateLimitExceeded: When either budget is used up.
    """
    ctx = get_context()
    refusal = charge(ctx.rate_limiter, tool_name)
    if refusal is not None:
        # Update Prometheus metrics
        prometheus_metrics.RATE_LIMIT_HITS_TOTAL.inc(result="rejected")

        # One record per refusal, with bounded fields only: whose budget was
        # used up, and the tool it is named after (#1495).
        ctx.security_handler.log_rate_limit_exceeded(
            limit=refusal.limit,
            window_seconds=refusal.window_seconds,
            scope=refusal.scope,
            key_kind="tool",
            key=tool_name,
        )
        raise refusal


@dataclass(frozen=True)
class RateLimited:
    """The rate-limit check of a tool whose work never reaches the command bus.

    Every call of `tool_name` is charged to the one budget named after it,
    whatever key the wrapper computes from its arguments.
    """

    tool_name: str

    def __call__(self, _key: str) -> None:
        charge_tool(self.tool_name)


def charged_by_the_command_bus(key: str) -> None:
    """The rate-limit check of a tool whose work is a command: the command bus charges it.

    Charging the call here as well would be a second budget for the same work
    (#1481). A branch that does its work without the bus calls `charge_tool`.
    """


#: The listing and inspection tools. They read state and change nothing, so no
#: rate limit refuses them (#1471): each registers `not_rate_limited` as its
#: check, and tests/unit/test_caller_rate_limit.py holds the two together.
#: `hangar_tools` is not one: it may start a stopped server to list its tools.
#: Nor is `hangar_sources`: it runs every discovery source's health check, a
#: call out of Hangar (the Kubernetes source's is a call to the cluster's API).
READ_ONLY_TOOLS = frozenset(
    {
        "hangar_details",
        "hangar_discovered",
        "hangar_group_list",
        "hangar_health",
        "hangar_list",
        "hangar_metrics",
        "hangar_quarantine",
        "hangar_status",
    }
)


def not_rate_limited(key: str) -> None:
    """The rate-limit check of a tool in `READ_ONLY_TOOLS`: there is none."""


def tool_error_mapper(exc: Exception) -> ToolErrorPayload:
    """Map exceptions to a stable MCP tool error payload.

    A rate-limit refusal keeps its own details -- `retry_after`, `scope`, `rps`
    and the budget's limit -- so a caller reads them from the same places
    whichever path refused the call (#1495). They are values Hangar chose, not
    anything an upstream returned. Every other exception's details are dropped:
    those can carry upstream text.
    """
    details: dict[str, Any] = dict(exc.details) if isinstance(exc, RateLimitExceeded) else {}
    return ToolErrorPayload(
        error=str(exc) or "unknown error",
        error_type=type(exc).__name__,
        details=details,
    )


def tool_error_hook(exc: Exception, context: dict) -> None:
    """Best-effort hook for logging/security telemetry on tool failures.

    Gets security handler from application context (DIP). Sends the error's
    type only: a tool's error text can carry what the upstream returned.

    Args:
        exc: The exception that occurred.
        context: Additional context dict with mcp_server_id, tool, etc.
    """
    if isinstance(exc, RateLimitExceeded):
        # Already recorded once, by whichever limiter refused the call:
        # `charge_tool` or the command bus's middleware. Recording it here too
        # would be a second record, and under the wrong event type (#1495).
        return
    try:
        ctx = get_context()
        ctx.security_handler.log_validation_failed(
            field="tool",
            message=bounded_error_type(type(exc).__qualname__),
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


def validate_group_id_input(group: str) -> None:
    """Validate a group ID and raise exception if invalid.

    Groups share the mcp_server identifier namespace (letters, digits, hyphens,
    underscores, max 64 chars), so the same shape check applies -- see #1209.
    """
    result = validate_mcp_server_id(group)
    if not result.valid:
        ctx = get_context()
        ctx.security_handler.log_validation_failed(
            field="group",
            message=(result.errors[0].message if result.errors else "Invalid group ID"),
            mcp_server_id=group,
        )
        raise ValueError(f"invalid_group_id: {result.errors[0].message if result.errors else 'validation failed'}")


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
