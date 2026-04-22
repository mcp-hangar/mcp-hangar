"""Alert event handler for critical notifications."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

from ...domain.events import DomainEvent, HealthCheckFailed, McpServerDegraded, McpServerStopped, ToolInvocationFailed
from ...logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Alert:
    """Represents an alert notification."""

    level: str  # critical, warning, info
    message: str
    mcp_server_id: str
    event_type: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def provider_id(self) -> str:
        return self.mcp_server_id

    def to_dict(self) -> dict[str, Any]:
        """Convert alert to dictionary."""
        return {
            "level": self.level,
            "message": self.message,
            "mcp_server_id": self.mcp_server_id,
            "provider_id": self.mcp_server_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details,
        }


class AlertSink(ABC):
    """Abstract interface for alert destinations."""

    @abstractmethod
    def send(self, alert: Alert) -> None:
        """Send an alert to the sink."""
        pass


class LogAlertSink(AlertSink):
    """Send alerts to the log."""

    def send(self, alert: Alert) -> None:
        """Log the alert."""
        log_method = {
            "critical": logger.critical,
            "warning": logger.warning,
            "info": logger.info,
        }.get(alert.level, logger.info)

        log_method(
            f"ALERT [{alert.level.upper()}] {alert.message} mcp_server={alert.mcp_server_id} event={alert.event_type}"
        )


class CallbackAlertSink(AlertSink):
    """Send alerts to a callback function."""

    def __init__(self, callback: Callable[[Alert], None]):
        self._callback = callback

    def send(self, alert: Alert) -> None:
        """Call the callback with the alert."""
        self._callback(alert)


class AlertEventHandler:
    """
    Event handler that generates alerts for critical events.

    Monitors domain events and generates alerts when:
    - McpServer is degraded
    - McpServer stops unexpectedly
    - Tool invocation fails
    - Health check fails repeatedly
    """

    def __init__(
        self,
        sinks: list[AlertSink] | None = None,
        degradation_threshold: int = 3,
        health_failure_threshold: int = 3,
    ):
        """
        Initialize the alert handler.

        Args:
            sinks: List of alert sinks to send alerts to
            degradation_threshold: Number of failures before critical alert
            health_failure_threshold: Consecutive health failures for warning
        """
        self._sinks = sinks or [LogAlertSink()]
        self._degradation_threshold = degradation_threshold
        self._health_failure_threshold = health_failure_threshold
        self._alerts_sent: list[Alert] = []

    def handle(self, event: DomainEvent) -> None:
        """Handle a domain event and potentially generate alerts."""
        if isinstance(event, McpServerDegraded):
            self._handle_degraded(event)
        elif isinstance(event, McpServerStopped):
            self._handle_stopped(event)
        elif isinstance(event, ToolInvocationFailed):
            self._handle_tool_failed(event)
        elif isinstance(event, HealthCheckFailed):
            self._handle_health_failed(event)

    def _handle_degraded(self, event: McpServerDegraded) -> None:
        """Handle mcp_server degraded event."""
        level = "critical" if event.consecutive_failures >= self._degradation_threshold else "warning"

        alert = Alert(
            level=level,
            message=f"McpServer degraded after {event.consecutive_failures} failures",
            mcp_server_id=event.mcp_server_id,
            event_type="McpServerDegraded",
            details={
                "consecutive_failures": event.consecutive_failures,
                "total_failures": event.total_failures,
                "reason": event.reason,
            },
        )
        self._send_alert(alert)

    def _handle_stopped(self, event: McpServerStopped) -> None:
        """Handle mcp_server stopped event."""
        # Only alert for unexpected stops (not shutdown or idle)
        if event.reason not in ("shutdown", "idle"):
            alert = Alert(
                level="warning",
                message=f"McpServer stopped unexpectedly: {event.reason}",
                mcp_server_id=event.mcp_server_id,
                event_type="McpServerStopped",
                details={"reason": event.reason},
            )
            self._send_alert(alert)

    def _handle_tool_failed(self, event: ToolInvocationFailed) -> None:
        """Handle tool invocation failed event."""
        alert = Alert(
            level="warning",
            message=f"Tool invocation failed: {event.tool_name}",
            mcp_server_id=event.mcp_server_id,
            event_type="ToolInvocationFailed",
            details={
                "tool_name": event.tool_name,
                "error_message": event.error_message,
                "error_type": event.error_type,
                "correlation_id": event.correlation_id,
            },
        )
        self._send_alert(alert)

    def _handle_health_failed(self, event: HealthCheckFailed) -> None:
        """Handle health check failed event."""
        if event.consecutive_failures >= self._health_failure_threshold:
            alert = Alert(
                level="warning",
                message=f"Health check failed {event.consecutive_failures} times",
                mcp_server_id=event.mcp_server_id,
                event_type="HealthCheckFailed",
                details={
                    "consecutive_failures": event.consecutive_failures,
                    "error_message": event.error_message,
                },
            )
            self._send_alert(alert)

    def _send_alert(self, alert: Alert) -> None:
        """Send alert to all sinks."""
        self._alerts_sent.append(alert)
        for sink in self._sinks:
            try:
                sink.send(alert)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: alert sink failure must not crash event handler
                logger.error(f"Failed to send alert to sink: {e}")

    @property
    def alerts_sent(self) -> list[Alert]:
        """Get list of alerts sent (for testing)."""
        return list(self._alerts_sent)

    def clear_alerts(self) -> None:
        """Clear sent alerts (for testing)."""
        self._alerts_sent.clear()


# --- Global alert handler instance ---

_alert_handler: AlertEventHandler | None = None


def get_alert_handler() -> AlertEventHandler:
    """Get or create the global alert handler instance."""
    global _alert_handler
    if _alert_handler is None:
        _alert_handler = AlertEventHandler()
    return _alert_handler


def reset_alert_handler() -> None:
    """Reset the global alert handler (for testing)."""
    global _alert_handler
    _alert_handler = None
