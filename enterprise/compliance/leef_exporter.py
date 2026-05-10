"""LEEF audit exporter for compliance events."""

from collections.abc import Callable
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from mcp_hangar.application.event_handlers.audit_handler import AuditRecord


logger = logging.getLogger(__name__)

LEEF_VERSION = "2.0"
DEVICE_VENDOR = "MCP Hangar"
DEVICE_PRODUCT = "MCP Hangar"
DEVICE_VERSION = "0.15.0"

_EVENT_ID_MAP: dict[str, str] = {
    "ToolInvocationRequested": "100",
    "ToolInvocationCompleted": "101",
    "ToolInvocationFailed": "102",
    "ProviderStateChanged": "202",
}


def _event_type_for_status(status: str) -> str:
    if status in ("success", "completed"):
        return "ToolInvocationCompleted"
    if status in ("error", "failure", "failed"):
        return "ToolInvocationFailed"
    return "ToolInvocationRequested"


def _escape_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def _format_dev_time(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%b %d %Y %H:%M:%S")


def _format_record(record: AuditRecord) -> str:
    data: dict[str, object] = dict(record.data or {})
    event_id = _EVENT_ID_MAP.get(record.event_type, "999")
    extensions: list[str] = [f"devTime={_escape_value(_format_dev_time(record.occurred_at))}"]

    protocol = "provider" if record.event_type == "ProviderStateChanged" else "tool"
    extensions.append(f"proto={protocol}")

    if record.caller_user_id:
        extensions.append(f"usrName={_escape_value(record.caller_user_id)}")
    if record.caller_session_id:
        extensions.append(f"sessID={_escape_value(record.caller_session_id)}")
    tool_name = data.get("tool_name")
    if tool_name is not None:
        extensions.append(f"action={_escape_value(str(tool_name))}")
    duration_ms = data.get("duration_ms")
    if duration_ms is not None:
        extensions.append(f"duration={duration_ms}")
    error_type = data.get("error_type")
    if error_type is not None:
        extensions.append(f"reason={_escape_value(str(error_type))}")
    from_state = data.get("from_state")
    if from_state is not None:
        extensions.append(f"oldState={_escape_value(str(from_state))}")
    to_state = data.get("to_state")
    if to_state is not None:
        extensions.append(f"newState={_escape_value(str(to_state))}")
    if record.provider_id:
        extensions.append(f"src={_escape_value(record.provider_id)}")

    header = f"LEEF:{LEEF_VERSION}|{DEVICE_VENDOR}|{DEVICE_PRODUCT}|{DEVICE_VERSION}|{event_id}|"
    return header + "\t" + "\t".join(extensions)


class LEEFExporter:
    def __init__(
        self,
        *,
        output_path: str | Path | None = None,
        output_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._output_fn: Callable[[str], None] | None = output_fn
        self._output_path: Path | None = Path(output_path) if output_path else None
        self._lines_exported: int = 0

    def _emit(self, leef_line: str) -> None:
        if self._output_fn is not None:
            _ = self._output_fn(leef_line)
        elif self._output_path is not None:
            try:
                with self._output_path.open("a", encoding="utf-8") as f:
                    _ = f.write(leef_line + "\n")
            except OSError as e:
                logger.error("Failed to write LEEF line to %s: %s", self._output_path, e)
                return
        else:
            _ = sys.stderr.write(leef_line + "\n")

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
