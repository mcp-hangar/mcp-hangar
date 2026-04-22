"""OTLP audit exporter for security-relevant domain events.

Exports tool invocations and mcp_server state transitions as OTLP log records.
Uses opentelemetry-api logs bridge when available; falls back to no-op.

MIT licensed -- part of core observability infrastructure.
"""

import time

from ...logging_config import get_logger
from ...observability.conventions import MCP, McpServer

logger = get_logger(__name__)

# Try to import OTEL logs API
try:
    from opentelemetry._logs import get_logger as otel_get_logger
    from opentelemetry._logs import SeverityNumber
    from opentelemetry.sdk._logs import LoggerMcpServer  # noqa: F401

    OTEL_LOGS_AVAILABLE = True
except ImportError:
    OTEL_LOGS_AVAILABLE = False


class OTLPAuditExporter:
    """Exports security-relevant events as OTLP log records.

    Each tool invocation and mcp_server state change is exported with
    MCP governance attributes (mcp.server.id, mcp.tool.name, etc.)
    so OTEL-compatible backends can filter and alert on them.

    Export failures are logged at WARNING level and never propagated
    to callers -- observability must not affect correctness.
    """

    def _emit_log_record(self, attributes: dict) -> None:
        """Emit a structured log record with the given attributes.

        This method is the actual OTLP emission point. It is extracted
        to a separate method to allow unit testing via mock patching.

        Args:
            attributes: Dict of MCP governance attributes to include.
        """
        if not OTEL_LOGS_AVAILABLE:
            # Fallback: emit via structlog so the event is not lost entirely
            logger.info("audit_event", **attributes)
            return

        otel_logger = otel_get_logger("mcp_hangar.audit")
        from opentelemetry._logs import LogRecord

        record = LogRecord(
            timestamp=int(time.time_ns()),
            severity_number=SeverityNumber.INFO,
            severity_text="INFO",
            body=attributes.get("mcp.event.name", "mcp.audit"),
            attributes=attributes,
        )
        otel_logger.emit(record)

    def export_tool_invocation(
        self,
        mcp_server_id: str,
        tool_name: str,
        status: str,
        duration_ms: float,
        user_id: str | None = None,
        session_id: str | None = None,
        error_type: str | None = None,
    ) -> None:
        """Export a tool invocation event as an audit log record.

        Args:
            mcp_server_id: McpServer that handled the tool call.
            tool_name: Tool that was invoked.
            status: Outcome -- "success", "error", "timeout", "blocked".
            duration_ms: Call duration in milliseconds.
            user_id: Optional calling user identity.
            session_id: Optional MCP session identifier.
            error_type: Optional exception class name on error.
        """
        try:
            attributes: dict = {
                "mcp.event.name": "tool_invocation",
                McpServer.ID: mcp_server_id,
                MCP.TOOL_NAME: tool_name,
                MCP.TOOL_STATUS: status,
                MCP.TOOL_DURATION_MS: duration_ms,
            }
            if user_id is not None:
                attributes[MCP.USER_ID] = user_id
            if session_id is not None:
                attributes[MCP.SESSION_ID] = session_id
            if error_type is not None:
                attributes["mcp.error.type"] = error_type

            self._emit_log_record(attributes)

        except Exception as e:  # noqa: BLE001 -- fault-barrier: export failures must not crash event handlers
            logger.warning(
                "otlp_audit_export_failed",
                audit_event="tool_invocation",
                mcp_server_id=mcp_server_id,
                tool_name=tool_name,
                error=str(e),
            )

    def export_mcp_server_state_change(
        self,
        mcp_server_id: str,
        from_state: str,
        to_state: str,
    ) -> None:
        """Export a mcp_server state transition as an audit log record.

        Args:
            mcp_server_id: McpServer that transitioned.
            from_state: Previous state.
            to_state: New state.
        """
        try:
            attributes: dict = {
                "mcp.event.name": "mcp_server_state_change",
                McpServer.ID: mcp_server_id,
                McpServer.STATE: to_state,
                "mcp.server.previous_state": from_state,
            }
            self._emit_log_record(attributes)

        except Exception as e:  # noqa: BLE001 -- fault-barrier: export failures must not crash event handlers
            logger.warning(
                "otlp_audit_export_failed",
                audit_event="mcp_server_state_change",
                mcp_server_id=mcp_server_id,
                error=str(e),
            )
