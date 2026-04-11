"""Unit tests for OTLPAuditEventHandler domain event -> IAuditExporter wiring."""

import pytest
from unittest.mock import MagicMock

from mcp_hangar.application.ports.observability import NullAuditExporter
from mcp_hangar.domain.events import (
    ProviderStateChanged,
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

        event = ToolInvocationCompleted(
            provider_id="math",
            tool_name="add",
            correlation_id="corr-1",
            duration_ms=10.5,
            result_size_bytes=256,
        )
        handler.handle(event)

        mock_exporter.export_tool_invocation.assert_called_once()
        call_kwargs = mock_exporter.export_tool_invocation.call_args[1]
        assert call_kwargs["provider_id"] == "math"
        assert call_kwargs["tool_name"] == "add"
        assert call_kwargs["status"] == "success"
        assert call_kwargs["duration_ms"] == 10.5

    def test_tool_invocation_failed_calls_exporter_with_error_status(self) -> None:
        from mcp_hangar.application.event_handlers.audit_event_handler import (
            OTLPAuditEventHandler,
        )

        mock_exporter = MagicMock()
        handler = OTLPAuditEventHandler(audit_exporter=mock_exporter)

        event = ToolInvocationFailed(
            provider_id="math",
            tool_name="add",
            correlation_id="corr-2",
            duration_ms=50.0,
            error_message="boom",
            error_type="ToolInvocationError",
        )
        handler.handle(event)

        mock_exporter.export_tool_invocation.assert_called_once()
        call_kwargs = mock_exporter.export_tool_invocation.call_args[1]
        assert call_kwargs["status"] == "error"
        assert call_kwargs["error_type"] == "ToolInvocationError"

    def test_provider_state_changed_calls_exporter(self) -> None:
        from mcp_hangar.application.event_handlers.audit_event_handler import (
            OTLPAuditEventHandler,
        )

        mock_exporter = MagicMock()
        handler = OTLPAuditEventHandler(audit_exporter=mock_exporter)

        event = ProviderStateChanged(
            provider_id="math",
            old_state="READY",
            new_state="DEGRADED",
        )
        handler.handle(event)

        mock_exporter.export_provider_state_change.assert_called_once_with(
            provider_id="math",
            from_state="READY",
            to_state="DEGRADED",
        )

    def test_handler_uses_null_exporter_when_none_provided(self) -> None:
        """Handler must work with NullAuditExporter (no crash, no output)."""
        from mcp_hangar.application.event_handlers.audit_event_handler import (
            OTLPAuditEventHandler,
        )

        handler = OTLPAuditEventHandler(audit_exporter=NullAuditExporter())
        event = ToolInvocationCompleted(
            provider_id="p",
            tool_name="t",
            correlation_id="corr-3",
            duration_ms=1.0,
            result_size_bytes=0,
        )
        handler.handle(event)  # must not raise
