"""CEF compliance exporter -- implements IAuditExporter protocol.

Exports security-relevant events (tool invocations, provider state changes)
as CEF (Common Event Format) log lines. Output is written to a configurable
destination: file, stderr, or a callback function.

This module is part of the enterprise compliance layer.
"""

import logging
import sys
from datetime import datetime, UTC
from pathlib import Path
from collections.abc import Callable

from mcp_hangar.application.event_handlers.audit_handler import AuditRecord
from .cef_formatter import format_audit_record


logger = logging.getLogger(__name__)


class CEFExporter:
    """Exports audit events as CEF log lines.

    Implements the IAuditExporter protocol from
    mcp_hangar.application.ports.observability.

    Output modes:
      - File: Appends CEF lines to a log file.
      - Callback: Calls a user-provided function with each CEF line.
      - Stderr: Writes to stderr (default, for container log collection).
    """

    def __init__(
        self,
        *,
        output_path: str | Path | None = None,
        output_fn: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the CEF exporter.

        Args:
            output_path: Path to a file for CEF output. If set, lines are
                appended to this file.
            output_fn: Callable that receives each CEF line. If set, takes
                priority over output_path and stderr.
        """
        self._output_fn = output_fn
        self._output_path = Path(output_path) if output_path else None
        self._lines_exported: int = 0

    def _emit(self, cef_line: str) -> None:
        """Write a single CEF line to the configured output.

        Args:
            cef_line: Formatted CEF string (no trailing newline).
        """
        if self._output_fn is not None:
            self._output_fn(cef_line)
        elif self._output_path is not None:
            try:
                with self._output_path.open("a", encoding="utf-8") as f:
                    f.write(cef_line + "\n")
            except OSError as e:
                logger.error("Failed to write CEF line to %s: %s", self._output_path, e)
                return
        else:
            sys.stderr.write(cef_line + "\n")

        self._lines_exported += 1

    def export_tool_invocation(
        self,
        mcp_server_id: str,
        tool_name: str,
        status: str,
        duration_ms: float,
        user_id: str | None = None,
        session_id: str | None = None,
        error_type: str | None = None,
        caller_type: str | None = None,
        caller_id: str | None = None,
        caller_roles: str | None = None,
        cost_cents: int | None = None,
        cost_model: str | None = None,
        cost_input_tokens: int | None = None,
        cost_output_tokens: int | None = None,
    ) -> None:
        """Export a tool invocation event as a CEF log line.

        Constructs an AuditRecord from the parameters and formats it as CEF.
        The event_type is derived from the status:
          - "success" -> ToolInvocationCompleted
          - "error"/"failure" -> ToolInvocationFailed
          - other -> ToolInvocationRequested
        """
        if status in ("success", "completed"):
            event_type = "ToolInvocationCompleted"
        elif status in ("error", "failure", "failed"):
            event_type = "ToolInvocationFailed"
        else:
            event_type = "ToolInvocationRequested"

        data: dict[str, str | float | int] = {
            "tool_name": tool_name,
            "duration_ms": duration_ms,
        }
        if error_type:
            data["error_type"] = error_type
        if caller_type:
            data["caller_type"] = caller_type
        if caller_id:
            data["caller_id"] = caller_id
        if caller_roles:
            data["caller_roles"] = caller_roles
        if cost_cents is not None:
            data["cost_cents"] = cost_cents
        if cost_model:
            data["cost_model"] = cost_model
        if cost_input_tokens is not None:
            data["cost_input_tokens"] = cost_input_tokens
        if cost_output_tokens is not None:
            data["cost_output_tokens"] = cost_output_tokens

        record = AuditRecord(
            event_id="",
            event_type=event_type,
            occurred_at=datetime.now(UTC),
            mcp_server_id=mcp_server_id,
            data=data,
            caller_user_id=user_id,
            caller_session_id=session_id,
        )

        cef_line = format_audit_record(record)
        self._emit(cef_line)

    def export_provider_state_change(
        self,
        provider_id: str,
        from_state: str,
        to_state: str,
    ) -> None:
        """Export a provider state transition as a CEF log line.

        .. deprecated::
            Use :meth:`export_mcp_server_state_change` instead.
            Planned removal: 2026-Q3.
        """
        import warnings

        warnings.warn(
            "export_provider_state_change is deprecated, use export_mcp_server_state_change instead. "
            "Planned removal: 2026-Q3.",
            DeprecationWarning,
            stacklevel=2,
        )
        record = AuditRecord(
            event_id="",
            event_type="ProviderStateChanged",
            occurred_at=datetime.now(UTC),
            mcp_server_id=provider_id,
            data={
                "from_state": from_state,
                "to_state": to_state,
            },
        )

        cef_line = format_audit_record(record)
        self._emit(cef_line)

    def export_mcp_server_state_change(self, mcp_server_id: str, from_state: str, to_state: str) -> None:
        """Export an MCP server state transition as a CEF log line."""
        record = AuditRecord(
            event_id="",
            event_type="ProviderStateChanged",
            occurred_at=datetime.now(UTC),
            mcp_server_id=mcp_server_id,
            data={
                "from_state": from_state,
                "to_state": to_state,
            },
        )

        cef_line = format_audit_record(record)
        self._emit(cef_line)

    @property
    def lines_exported(self) -> int:
        """Total number of CEF lines emitted."""
        return self._lines_exported
