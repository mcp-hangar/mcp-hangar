"""Unit tests for OTLPAuditExporter."""

from unittest.mock import patch


class TestOTLPAuditExporter:
    """OTLPAuditExporter must emit log records for security-relevant events."""

    def test_export_tool_invocation_success_emits_log_record(self) -> None:
        from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter
        from mcp_hangar.observability.conventions import MCP, McpServer

        exporter = OTLPAuditExporter()

        with patch.object(exporter, "_emit_log_record") as mock_emit:
            exporter.export_tool_invocation(mcp_server_id="math", tool_name="add",
            status="success",
            duration_ms=12.5,)
            mock_emit.assert_called_once()
            record = mock_emit.call_args[0][0]
            assert record.get(McpServer.ID) == "math"
            assert record.get(MCP.TOOL_NAME) == "add"
            assert record.get(MCP.TOOL_STATUS) == "success"

    def test_export_tool_invocation_error_includes_error_type(self) -> None:
        from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter

        exporter = OTLPAuditExporter()

        with patch.object(exporter, "_emit_log_record") as mock_emit:
            exporter.export_tool_invocation(mcp_server_id="p", tool_name="t",
            status="error",
            duration_ms=5.0,
            error_type="ToolInvocationError",)
            record = mock_emit.call_args[0][0]
            assert record.get("mcp.tool.status") == "error"
            assert record.get("mcp.error.type") == "ToolInvocationError"

    def test_export_mcp_server_state_change_emits_log_record(self) -> None:
        from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter
        from mcp_hangar.observability.conventions import McpServer

        exporter = OTLPAuditExporter()

        with patch.object(exporter, "_emit_log_record") as mock_emit:
            exporter.export_mcp_server_state_change("math", "READY", "DEGRADED")
            record = mock_emit.call_args[0][0]
            assert record.get(McpServer.ID) == "math"
            assert record.get(McpServer.STATE) == "DEGRADED"
            assert record.get("mcp.server.previous_state") == "READY"

    def test_export_failure_does_not_raise(self) -> None:
        """Exporter must swallow export errors to avoid crashing event handlers."""
        from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter

        exporter = OTLPAuditExporter()

        with patch.object(exporter, "_emit_log_record", side_effect=RuntimeError("OTLP unavailable")):
            # Must not raise
            exporter.export_tool_invocation("p", "t", "success", 1.0)

    def test_null_exporter_is_no_op(self) -> None:
        from mcp_hangar.application.ports.observability import NullAuditExporter

        exporter = NullAuditExporter()
        exporter.export_tool_invocation("p", "t", "success", 1.0)
        exporter.export_mcp_server_state_change("p", "COLD", "READY")
        # No error, no output
