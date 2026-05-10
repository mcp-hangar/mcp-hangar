"""OTLP audit event handler -- bridges domain events to IAuditExporter.

Subscribes to tool invocation and mcp_server state events. Forwards them
to IAuditExporter (OTLPAuditExporter in production, NullAuditExporter
when OTLP not configured).

MIT licensed -- part of core event handler infrastructure.
"""

from ...domain.contracts.cost import ICostAttributor, InvocationContext, NullCostAttributor
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

    def __init__(
        self,
        audit_exporter: IAuditExporter | None = None,
        cost_attributor: ICostAttributor | None = None,
    ) -> None:
        self._exporter = audit_exporter or NullAuditExporter()
        self._cost_attributor = cost_attributor or NullCostAttributor()

    def handle(self, event: object) -> None:
        if isinstance(event, ToolInvocationCompleted):
            identity = event.identity_context or {}
            cost_record = self._cost_attributor.compute_cost(
                InvocationContext(
                    mcp_server_id=event.mcp_server_id,
                    tool_name=event.tool_name,
                    duration_ms=event.duration_ms,
                    correlation_id=event.correlation_id,
                )
            )
            self._exporter.export_tool_invocation(
                mcp_server_id=event.mcp_server_id,
                tool_name=event.tool_name,
                status="success",
                duration_ms=event.duration_ms,
                caller_type=identity.get("principal_type"),
                caller_id=identity.get("principal_id"),
                caller_roles=identity.get("roles"),
                cost_cents=cost_record.cost_cents if cost_record.cost_cents else None,
                cost_model=str(cost_record.cost_model) if cost_record.cost_cents else None,
                cost_input_tokens=cost_record.input_tokens if cost_record.input_tokens else None,
                cost_output_tokens=cost_record.output_tokens if cost_record.output_tokens else None,
            )
        elif isinstance(event, ToolInvocationFailed):
            identity = event.identity_context or {}
            self._exporter.export_tool_invocation(
                mcp_server_id=event.mcp_server_id,
                tool_name=event.tool_name,
                status="error",
                duration_ms=0.0,
                error_type=event.error_type,
                caller_type=identity.get("principal_type"),
                caller_id=identity.get("principal_id"),
                caller_roles=identity.get("roles"),
            )
        elif isinstance(event, McpServerStateChanged):
            self._exporter.export_mcp_server_state_change(
                mcp_server_id=event.mcp_server_id,
                from_state=event.old_state,
                to_state=event.new_state,
            )
