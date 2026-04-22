"""Tests for Alert Event Handler."""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.event_handlers.alert_handler import (
    Alert,
    AlertEventHandler,
    AlertSink,
    CallbackAlertSink,
    LogAlertSink,
)
from mcp_hangar.domain.events import (
    HealthCheckFailed,
    McpServerDegraded,
    McpServerStarted,
    McpServerStopped,
    ToolInvocationFailed,
)


class TestAlert:
    """Test Alert dataclass."""

    def test_alert_creation(self):
        """Test creating an alert."""
        alert = Alert(
            level="critical",
            message="Provider died",
            mcp_server_id="test-provider",
            event_type="McpServerDegraded",
            details={"error": "connection lost"},
        )

        assert alert.level == "critical"
        assert alert.message == "Provider died"
        assert alert.mcp_server_id == "test-provider"
        assert alert.event_type == "McpServerDegraded"
        assert alert.details == {"error": "connection lost"}
        assert isinstance(alert.timestamp, datetime)

    def test_alert_to_dict(self):
        """Test alert to dictionary conversion."""
        alert = Alert(
            level="warning",
            message="Provider degraded",
            mcp_server_id="test",
            event_type="McpServerDegraded",
            details={},
        )

        d = alert.to_dict()

        assert d["level"] == "warning"
        assert d["event_type"] == "McpServerDegraded"
        assert d["mcp_server_id"] == "test"
        assert d["message"] == "Provider degraded"
        assert "timestamp" in d
        assert d["details"] == {}


class TestLogAlertSink:
    """Test LogAlertSink implementation."""

    def test_log_alert_sink_logs_critical(self):
        """Test that LogAlertSink logs critical alerts."""
        sink = LogAlertSink()

        alert = Alert(
            level="critical",
            message="Test alert",
            mcp_server_id="test",
            event_type="McpServerDegraded",
            details={},
        )

        with patch("mcp_hangar.application.event_handlers.alert_handler.logger") as mock_logger:
            sink.send(alert)
            mock_logger.critical.assert_called()

    def test_log_alert_sink_warning_level(self):
        """Test warning level uses warning log level."""
        sink = LogAlertSink()

        alert = Alert(
            level="warning",
            message="Test warning",
            mcp_server_id="test",
            event_type="McpServerDegraded",
            details={},
        )

        with patch("mcp_hangar.application.event_handlers.alert_handler.logger") as mock_logger:
            sink.send(alert)
            mock_logger.warning.assert_called()

    def test_log_alert_sink_info_level(self):
        """Test info level uses info log level."""
        sink = LogAlertSink()

        alert = Alert(
            level="info",
            message="Test info",
            mcp_server_id="test",
            event_type="ToolInvocationFailed",
            details={},
        )

        with patch("mcp_hangar.application.event_handlers.alert_handler.logger") as mock_logger:
            sink.send(alert)
            mock_logger.info.assert_called()


class TestCallbackAlertSink:
    """Test CallbackAlertSink implementation."""

    def test_callback_sink_calls_callback(self):
        """Test that callback sink calls the provided callback."""
        callback = Mock()
        sink = CallbackAlertSink(callback)

        alert = Alert(
            level="critical",
            message="Test alert",
            mcp_server_id="test",
            event_type="McpServerDegraded",
            details={},
        )

        sink.send(alert)

        callback.assert_called_once_with(alert)

    def test_callback_sink_passes_alert_data(self):
        """Test that callback receives correct alert data."""
        received_alerts = []

        def capture_alert(alert):
            received_alerts.append(alert)

        sink = CallbackAlertSink(capture_alert)

        alert = Alert(
            level="warning",
            message="Degraded",
            mcp_server_id="p1",
            event_type="McpServerDegraded",
            details={"failures": 3},
        )

        sink.send(alert)

        assert len(received_alerts) == 1
        assert received_alerts[0].mcp_server_id == "p1"
        assert received_alerts[0].details["failures"] == 3


class TestAlertEventHandler:
    """Test AlertEventHandler."""

    def test_handler_with_default_sink(self):
        """Test handler uses LogAlertSink by default."""
        handler = AlertEventHandler()

        assert len(handler._sinks) == 1
        assert isinstance(handler._sinks[0], LogAlertSink)

    def test_handler_with_custom_sinks(self):
        """Test handler with custom sinks."""
        callback = Mock()
        custom_sink = CallbackAlertSink(callback)
        handler = AlertEventHandler(sinks=[custom_sink])

        assert len(handler._sinks) == 1
        assert handler._sinks[0] is custom_sink

    def test_handle_provider_degraded_event_warning(self):
        """Test handling McpServerDegraded event with low failures creates warning alert."""
        alerts = []
        sink = CallbackAlertSink(alerts.append)
        handler = AlertEventHandler(sinks=[sink], degradation_threshold=5)

        event = McpServerDegraded(mcp_server_id="test-provider", consecutive_failures=2,
        total_failures=5,
        reason="timeout",)

        handler.handle(event)

        assert len(alerts) == 1
        assert alerts[0].level == "warning"
        assert alerts[0].event_type == "McpServerDegraded"
        assert alerts[0].mcp_server_id == "test-provider"

    def test_handle_provider_degraded_event_critical(self):
        """Test handling McpServerDegraded event with high failures creates critical alert."""
        alerts = []
        sink = CallbackAlertSink(alerts.append)
        handler = AlertEventHandler(sinks=[sink], degradation_threshold=3)

        event = McpServerDegraded(mcp_server_id="test-provider", consecutive_failures=5,
        total_failures=10,
        reason="timeout",)

        handler.handle(event)

        assert len(alerts) == 1
        assert alerts[0].level == "critical"

    def test_handle_provider_stopped_unexpected(self):
        """Test handling McpServerStopped with unexpected reason creates warning."""
        alerts = []
        sink = CallbackAlertSink(alerts.append)
        handler = AlertEventHandler(sinks=[sink])

        event = McpServerStopped(mcp_server_id="test-provider", reason="error")

        handler.handle(event)

        assert len(alerts) == 1
        assert alerts[0].level == "warning"
        assert alerts[0].event_type == "McpServerStopped"

    def test_handle_provider_stopped_normal_no_alert(self):
        """Test McpServerStopped with normal reason doesn't trigger alerts."""
        alerts = []
        sink = CallbackAlertSink(alerts.append)
        handler = AlertEventHandler(sinks=[sink])

        # shutdown is normal
        event = McpServerStopped(mcp_server_id="test-provider", reason="shutdown")
        handler.handle(event)

        # idle is normal
        event2 = McpServerStopped(mcp_server_id="test-provider", reason="idle")
        handler.handle(event2)

        assert len(alerts) == 0

    def test_handle_tool_invocation_failed_event(self):
        """Test handling ToolInvocationFailed event creates warning alert."""
        alerts = []
        sink = CallbackAlertSink(alerts.append)
        handler = AlertEventHandler(sinks=[sink])

        event = ToolInvocationFailed(mcp_server_id="test-provider", tool_name="add",
        correlation_id="corr-123",
        duration_ms=50.0,
        error_message="timeout",
        error_type="TimeoutError",)

        handler.handle(event)

        assert len(alerts) == 1
        assert alerts[0].level == "warning"
        assert alerts[0].event_type == "ToolInvocationFailed"

    def test_handle_health_check_failed_below_threshold(self):
        """Test HealthCheckFailed below threshold doesn't trigger alert."""
        alerts = []
        sink = CallbackAlertSink(alerts.append)
        handler = AlertEventHandler(sinks=[sink], health_failure_threshold=5)

        event = HealthCheckFailed(mcp_server_id="test-provider", consecutive_failures=2,
        error_message="connection refused",)

        handler.handle(event)

        assert len(alerts) == 0

    def test_handle_health_check_failed_above_threshold(self):
        """Test HealthCheckFailed above threshold triggers alert."""
        alerts = []
        sink = CallbackAlertSink(alerts.append)
        handler = AlertEventHandler(sinks=[sink], health_failure_threshold=3)

        event = HealthCheckFailed(mcp_server_id="test-provider", consecutive_failures=5,
        error_message="connection refused",)

        handler.handle(event)

        assert len(alerts) == 1
        assert alerts[0].level == "warning"
        assert alerts[0].event_type == "HealthCheckFailed"

    def test_handle_non_alertable_event(self):
        """Test handling non-alertable events does nothing."""
        alerts = []
        sink = CallbackAlertSink(alerts.append)
        handler = AlertEventHandler(sinks=[sink])

        # McpServerStarted is not an alertable event
        event = McpServerStarted(mcp_server_id="test-provider", mode="subprocess",
        tools_count=5,
        startup_duration_ms=100.0,)

        handler.handle(event)

        assert len(alerts) == 0

    def test_alert_includes_timestamp(self):
        """Test that alerts include timestamp."""
        alerts = []
        sink = CallbackAlertSink(alerts.append)
        handler = AlertEventHandler(sinks=[sink], degradation_threshold=1)

        event = McpServerDegraded(mcp_server_id="test", consecutive_failures=2, total_failures=5, reason="error")

        handler.handle(event)

        assert isinstance(alerts[0].timestamp, datetime)

    def test_alert_includes_context(self):
        """Test that alerts include relevant context."""
        alerts = []
        sink = CallbackAlertSink(alerts.append)
        handler = AlertEventHandler(sinks=[sink])

        event = McpServerDegraded(mcp_server_id="test", consecutive_failures=5,
        total_failures=10,
        reason="timeout",)

        handler.handle(event)

        assert "consecutive_failures" in alerts[0].details
        assert alerts[0].details["consecutive_failures"] == 5
        assert alerts[0].details["total_failures"] == 10
        assert alerts[0].details["reason"] == "timeout"

    def test_multiple_events_create_multiple_alerts(self):
        """Test multiple events create multiple alerts."""
        alerts = []
        sink = CallbackAlertSink(alerts.append)
        handler = AlertEventHandler(sinks=[sink])

        handler.handle(
            McpServerDegraded(mcp_server_id="p1", consecutive_failures=3,
            total_failures=5,
            reason="error",)
        )
        handler.handle(
            ToolInvocationFailed(mcp_server_id="p2", tool_name="add",
            correlation_id="c1",
            duration_ms=50.0,
            error_message="timeout",
            error_type="TimeoutError",)
        )

        assert len(alerts) == 2
        assert alerts[0].mcp_server_id == "p1"
        assert alerts[1].mcp_server_id == "p2"

    def test_alerts_sent_property(self):
        """Test alerts_sent property returns list of sent alerts."""
        alerts = []
        sink = CallbackAlertSink(alerts.append)
        handler = AlertEventHandler(sinks=[sink])

        handler.handle(
            McpServerDegraded(mcp_server_id="p1", consecutive_failures=3,
            total_failures=5,
            reason="error",)
        )

        assert len(handler.alerts_sent) == 1
        assert handler.alerts_sent[0].mcp_server_id == "p1"

    def test_clear_alerts(self):
        """Test clear_alerts clears the sent alerts list."""
        handler = AlertEventHandler()

        handler.handle(
            McpServerDegraded(mcp_server_id="p1", consecutive_failures=3,
            total_failures=5,
            reason="error",)
        )

        assert len(handler.alerts_sent) == 1

        handler.clear_alerts()

        assert len(handler.alerts_sent) == 0


class TestAlertSinkInterface:
    """Test AlertSink abstract interface."""

    def test_sink_requires_send_method(self):
        """Test AlertSink requires send method."""
        with pytest.raises(TypeError):

            class IncompleteSink(AlertSink):
                pass

            IncompleteSink()

    def test_custom_sink_implementation(self):
        """Test custom sink implementation."""

        class CustomSink(AlertSink):
            def __init__(self):
                self.alerts = []

            def send(self, alert: Alert) -> None:
                self.alerts.append(alert.to_dict())

        sink = CustomSink()
        alert = Alert(
            level="critical",
            message="Test",
            mcp_server_id="p1",
            event_type="Test",
            details={},
        )

        sink.send(alert)

        assert len(sink.alerts) == 1
        assert sink.alerts[0]["mcp_server_id"] == "p1"
