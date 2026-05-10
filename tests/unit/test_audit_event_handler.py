"""Unit tests for OTLPAuditEventHandler domain event -> IAuditExporter wiring."""

from unittest.mock import MagicMock

from mcp_hangar.application.ports.observability import NullAuditExporter
from mcp_hangar.domain.events import (
    McpServerStateChanged,
    ToolInvocationCompleted,
    ToolInvocationFailed,
)


class TestOTLPAuditEventHandler:
    """OTLPAuditEventHandler forwards domain events to IAuditExporter."""

    def test_tool_invocation_completed_calls_exporter(self) -> None:
        from mcp_hangar.application.event_handlers.audit_event_handler import (
            OTLPAuditEventHandler,
        )

        mock_exporter = MagicMock()
        handler = OTLPAuditEventHandler(audit_exporter=mock_exporter)

        event = ToolInvocationCompleted(mcp_server_id="math", tool_name="add",
        correlation_id="corr-1",
        duration_ms=10.5,
        result_size_bytes=256,)
        handler.handle(event)

        mock_exporter.export_tool_invocation.assert_called_once()
        call_kwargs = mock_exporter.export_tool_invocation.call_args[1]
        assert call_kwargs["mcp_server_id"] == "math"
        assert call_kwargs["tool_name"] == "add"
        assert call_kwargs["status"] == "success"
        assert call_kwargs["duration_ms"] == 10.5

    def test_tool_invocation_failed_calls_exporter_with_error_status(self) -> None:
        from mcp_hangar.application.event_handlers.audit_event_handler import (
            OTLPAuditEventHandler,
        )

        mock_exporter = MagicMock()
        handler = OTLPAuditEventHandler(audit_exporter=mock_exporter)

        event = ToolInvocationFailed(mcp_server_id="math", tool_name="add",
        correlation_id="corr-2",
        duration_ms=50.0,
        error_message="boom",
        error_type="ToolInvocationError",)
        handler.handle(event)

        mock_exporter.export_tool_invocation.assert_called_once()
        call_kwargs = mock_exporter.export_tool_invocation.call_args[1]
        assert call_kwargs["status"] == "error"
        assert call_kwargs["error_type"] == "ToolInvocationError"

    def test_mcp_server_state_changed_calls_exporter(self) -> None:
        from mcp_hangar.application.event_handlers.audit_event_handler import (
            OTLPAuditEventHandler,
        )

        mock_exporter = MagicMock()
        handler = OTLPAuditEventHandler(audit_exporter=mock_exporter)

        event = McpServerStateChanged(mcp_server_id="math", old_state="READY",
        new_state="DEGRADED",)
        handler.handle(event)

        mock_exporter.export_mcp_server_state_change.assert_called_once_with(mcp_server_id="math", from_state="READY",
        to_state="DEGRADED",)

    def test_handler_uses_null_exporter_when_none_provided(self) -> None:
        """Handler must work with NullAuditExporter (no crash, no output)."""
        from mcp_hangar.application.event_handlers.audit_event_handler import (
            OTLPAuditEventHandler,
        )

        handler = OTLPAuditEventHandler(audit_exporter=NullAuditExporter())
        event = ToolInvocationCompleted(mcp_server_id="p", tool_name="t",
        correlation_id="corr-3",
        duration_ms=1.0,
        result_size_bytes=0,)
        handler.handle(event)  # must not raise

    def test_cost_fields_propagated_when_attributor_configured(self) -> None:
        from mcp_hangar.application.event_handlers.audit_event_handler import (
            OTLPAuditEventHandler,
        )
        from mcp_hangar.domain.value_objects.cost import CostModel, CostRecord

        class StubCostAttributor:
            def compute_cost(self, context: object) -> CostRecord:
                return CostRecord(
                    mcp_server_id="math",
                    tool_name="add",
                    duration_ms=10.0,
                    cost_cents=42,
                    cost_model=CostModel.TOKEN,
                    input_tokens=100,
                    output_tokens=50,
                )

        mock_exporter = MagicMock()
        handler = OTLPAuditEventHandler(
            audit_exporter=mock_exporter,
            cost_attributor=StubCostAttributor(),
        )

        event = ToolInvocationCompleted(
            mcp_server_id="math",
            tool_name="add",
            correlation_id="corr-4",
            duration_ms=10.0,
            result_size_bytes=0,
        )
        handler.handle(event)

        call_kwargs = mock_exporter.export_tool_invocation.call_args[1]
        assert call_kwargs["cost_cents"] == 42
        assert call_kwargs["cost_model"] == "token"
        assert call_kwargs["cost_input_tokens"] == 100
        assert call_kwargs["cost_output_tokens"] == 50

    def test_cost_fields_none_when_no_cost(self) -> None:
        from mcp_hangar.application.event_handlers.audit_event_handler import (
            OTLPAuditEventHandler,
        )

        mock_exporter = MagicMock()
        handler = OTLPAuditEventHandler(audit_exporter=mock_exporter)

        event = ToolInvocationCompleted(
            mcp_server_id="math",
            tool_name="add",
            correlation_id="corr-5",
            duration_ms=10.0,
            result_size_bytes=0,
        )
        handler.handle(event)

        call_kwargs = mock_exporter.export_tool_invocation.call_args[1]
        assert call_kwargs["cost_cents"] is None
        assert call_kwargs["cost_model"] is None
