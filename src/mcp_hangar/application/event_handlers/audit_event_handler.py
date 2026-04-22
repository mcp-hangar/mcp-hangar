"""OTLP audit event handler -- bridges domain events to IAuditExporter.

Subscribes to tool invocation and mcp_server state events. Forwards them
to IAuditExporter (OTLPAuditExporter in production, NullAuditExporter
when OTLP not configured).

MIT licensed -- part of core event handler infrastructure.
"""

from ...domain.events import (
    McpServerStateChanged,
    ToolInvocationCompleted,
    ToolInvocationFailed,
)
from ...logging_config import get_logger
from ..ports.observability import IAuditExporter, NullAuditExporter

logger = get_logger(__name__)


class OTLPAuditEventHandler:
    """Forwards security-relevant domain events to the audit exporter.

    Designed to be registered with the event bus. Each handle() call
    is synchronous and completes before returning. Export failures are
    swallowed by the exporter (OTLPAuditExporter fault-barrier pattern).
    """

    def __init__(self, audit_exporter: IAuditExporter | None = None) -> None:
        """Initialize handler.

        Args:
            audit_exporter: Exporter to forward events to.
                Defaults to NullAuditExporter if None.
        """
        self._exporter = audit_exporter or NullAuditExporter()

    def handle(self, event: object) -> None:
        """Dispatch event to the appropriate exporter method.

        Args:
            event: A domain event. Handles ToolInvocationCompleted,
                ToolInvocationFailed, McpServerStateChanged. Ignores all others.
        """
        if isinstance(event, ToolInvocationCompleted):
            self._exporter.export_tool_invocation(
                mcp_server_id=event.mcp_server_id,
                tool_name=event.tool_name,
                status="success",
                duration_ms=event.duration_ms,
            )
        elif isinstance(event, ToolInvocationFailed):
            self._exporter.export_tool_invocation(
                mcp_server_id=event.mcp_server_id,
                tool_name=event.tool_name,
                status="error",
                duration_ms=0.0,
                error_type=event.error_type,
            )
        elif isinstance(event, McpServerStateChanged):
            self._exporter.export_mcp_server_state_change(
                mcp_server_id=event.mcp_server_id,
                from_state=event.old_state,
                to_state=event.new_state,
            )
