"""JSON-lines audit exporter for compliance events."""

from collections.abc import Callable
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from mcp_hangar.application.event_handlers.audit_handler import AuditRecord


logger = logging.getLogger(__name__)


def _event_type_for_status(status: str) -> str:
    if status in ("success", "completed"):
        return "ToolInvocationCompleted"
    if status in ("error", "failure", "failed"):
        return "ToolInvocationFailed"
    return "ToolInvocationRequested"


def _record_to_json_line(record: AuditRecord) -> str:
    data = record.data or {}
    payload = {
        "timestamp": record.occurred_at.isoformat(),
        "event_type": record.event_type,
        "provider_id": record.provider_id,
        "tool_name": data.get("tool_name"),
        "status": data.get("status"),
        "duration_ms": data.get("duration_ms"),
        "user_id": record.caller_user_id,
        "session_id": record.caller_session_id,
        "error_type": data.get("error_type"),
        "from_state": data.get("from_state"),
        "to_state": data.get("to_state"),
    }
    return json.dumps({key: value for key, value in payload.items() if value is not None})


class JSONLinesExporter:
    def __init__(
        self,
        *,
        output_path: str | Path | None = None,
        output_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._output_fn: Callable[[str], None] | None = output_fn
        self._output_path: Path | None = Path(output_path) if output_path else None
        self._lines_exported: int = 0

    def _emit(self, json_line: str) -> None:
        if self._output_fn is not None:
            _ = self._output_fn(json_line)
        elif self._output_path is not None:
            try:
                with self._output_path.open("a", encoding="utf-8") as f:
                    _ = f.write(json_line + "\n")
            except OSError as e:
                logger.error("Failed to write JSON-lines entry to %s: %s", self._output_path, e)
                return
        else:
            _ = sys.stderr.write(json_line + "\n")

        self._lines_exported += 1

    def export_tool_invocation(
        self,
        provider_id: str,
        tool_name: str,
        status: str,
        duration_ms: float,
        user_id: str | None = None,
        session_id: str | None = None,
        error_type: str | None = None,
    ) -> None:
        data: dict[str, str | float] = {
            "tool_name": tool_name,
            "status": status,
            "duration_ms": duration_ms,
        }
        if error_type:
            data["error_type"] = error_type

        record = AuditRecord(
            event_id="",
            event_type=_event_type_for_status(status),
            occurred_at=datetime.now(UTC),
            mcp_server_id=provider_id,
            data=data,
            caller_user_id=user_id,
            caller_session_id=session_id,
        )
        self._emit(_record_to_json_line(record))

    def export_provider_state_change(self, provider_id: str, from_state: str, to_state: str) -> None:
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
        self._emit(_record_to_json_line(record))

    def export_mcp_server_state_change(self, mcp_server_id: str, from_state: str, to_state: str) -> None:
        self.export_provider_state_change(mcp_server_id, from_state, to_state)

    @property
    def lines_exported(self) -> int:
        return self._lines_exported
