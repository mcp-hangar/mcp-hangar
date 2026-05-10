"""RFC 5424 syslog audit exporter for compliance events."""

from collections.abc import Callable
import logging
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

from mcp_hangar.application.event_handlers.audit_handler import AuditRecord


logger = logging.getLogger(__name__)

FACILITY_LOCAL0 = 16
APP_NAME = "mcp-hangar"
SD_ID = "mcp@49152"

_MSG_ID_MAP: dict[str, str] = {
    "ToolInvocationRequested": "100",
    "ToolInvocationCompleted": "101",
    "ToolInvocationFailed": "102",
    "ProviderStateChanged": "202",
}

_SEVERITY_MAP: dict[str, int] = {
    "ToolInvocationRequested": 6,
    "ToolInvocationCompleted": 6,
    "ToolInvocationFailed": 3,
    "ProviderStateChanged": 4,
}


def _event_type_for_status(status: str) -> str:
    if status in ("success", "completed"):
        return "ToolInvocationCompleted"
    if status in ("error", "failure", "failed"):
        return "ToolInvocationFailed"
    return "ToolInvocationRequested"


def _escape_sd_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")


def _format_structured_data(record: AuditRecord) -> str:
    data: dict[str, object] = dict(record.data or {})
    fields = {
        "provider": record.provider_id,
        "tool": data.get("tool_name"),
        "status": data.get("status"),
        "duration": data.get("duration_ms"),
        "user": record.caller_user_id,
        "session": record.caller_session_id,
        "error": data.get("error_type"),
        "fromState": data.get("from_state"),
        "toState": data.get("to_state"),
    }
    params = " ".join(
        f'{key}="{_escape_sd_value(str(value))}"' for key, value in fields.items() if value is not None
    )
    return f"[{SD_ID}{(' ' + params) if params else ''}]"


def _format_message(record: AuditRecord) -> str:
    data: dict[str, object] = dict(record.data or {})
    if record.event_type == "ProviderStateChanged":
        return f"Provider {record.provider_id} state changed from {data.get('from_state')} to {data.get('to_state')}"
    status = data.get("status")
    status_text = str(status).lower() if status is not None else record.event_type.lower()
    return (
        f"Tool {data.get('tool_name')} on provider {record.provider_id} "
        f"{status_text}"
    )


def _format_record(record: AuditRecord) -> str:
    severity = _SEVERITY_MAP.get(record.event_type, 6)
    pri = FACILITY_LOCAL0 * 8 + severity
    timestamp = record.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    hostname = socket.gethostname()
    procid = str(os.getpid())
    msgid = _MSG_ID_MAP.get(record.event_type, record.event_type)
    structured_data = _format_structured_data(record)
    message = _format_message(record)
    return f"<{pri}>1 {timestamp} {hostname} {APP_NAME} {procid} {msgid} {structured_data} {message}"


class SyslogExporter:
    def __init__(
        self,
        *,
        output_path: str | Path | None = None,
        output_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._output_fn: Callable[[str], None] | None = output_fn
        self._output_path: Path | None = Path(output_path) if output_path else None
        self._lines_exported: int = 0

    def _emit(self, syslog_line: str) -> None:
        if self._output_fn is not None:
            _ = self._output_fn(syslog_line)
        elif self._output_path is not None:
            try:
                with self._output_path.open("a", encoding="utf-8") as f:
                    _ = f.write(syslog_line + "\n")
            except OSError as e:
                logger.error("Failed to write syslog line to %s: %s", self._output_path, e)
                return
        else:
            _ = sys.stderr.write(syslog_line + "\n")

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
        data: dict[str, str | float | int] = {
            "tool_name": tool_name,
            "status": status,
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
            event_type=_event_type_for_status(status),
            occurred_at=datetime.now(UTC),
            mcp_server_id=mcp_server_id,
            data=data,
            caller_user_id=user_id,
            caller_session_id=session_id,
        )
        self._emit(_format_record(record))

    def export_provider_state_change(self, provider_id: str, from_state: str, to_state: str) -> None:
        """.. deprecated:: Use :meth:`export_mcp_server_state_change`. Removal: 2026-Q3."""
        import warnings

        warnings.warn(
            "export_provider_state_change is deprecated, use export_mcp_server_state_change instead. "
            "Planned removal: 2026-Q3.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.export_mcp_server_state_change(provider_id, from_state, to_state)

    def export_mcp_server_state_change(self, mcp_server_id: str, from_state: str, to_state: str) -> None:
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
        self._emit(_format_record(record))

    @property
    def lines_exported(self) -> int:
        return self._lines_exported
