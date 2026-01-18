"""Alert event handler for critical notifications."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ...domain.events import DomainEvent, HealthCheckFailed, ProviderDegraded, ProviderStopped, ToolInvocationFailed
from ...logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Alert:
    """Represents an alert notification."""

    level: str  # critical, warning, info
    message: str
    provider_id: str
    event_type: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert alert to dictionary."""
        return {
            "level": self.level,
            "message": self.message,
            "provider_id": self.provider_id,
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
            f"ALERT [{alert.level.upper()}] {alert.message} provider={alert.provider_id} event={alert.event_type}"
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
    - Provider is degraded
    - Provider stops unexpectedly
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
        if isinstance(event, ProviderDegraded):
            self._handle_degraded(event)
        elif isinstance(event, ProviderStopped):
            self._handle_stopped(event)
        elif isinstance(event, ToolInvocationFailed):
            self._handle_tool_failed(event)
        elif isinstance(event, HealthCheckFailed):
            self._handle_health_failed(event)

    def _handle_degraded(self, event: ProviderDegraded) -> None:
        """Handle provider degraded event."""
        level = "critical" if event.consecutive_failures >= self._degradation_threshold else "warning"

        alert = Alert(
            level=level,
            message=f"Provider degraded after {event.consecutive_failures} failures",
            provider_id=event.provider_id,
            event_type="ProviderDegraded",
            details={
                "consecutive_failures": event.consecutive_failures,
                "total_failures": event.total_failures,
                "reason": event.reason,
            },
        )
        self._send_alert(alert)

    def _handle_stopped(self, event: ProviderStopped) -> None:
        """Handle provider stopped event."""
        # Only alert for unexpected stops (not shutdown or idle)
        if event.reason not in ("shutdown", "idle"):
            alert = Alert(
                level="warning",
                message=f"Provider stopped unexpectedly: {event.reason}",
                provider_id=event.provider_id,
                event_type="ProviderStopped",
                details={"reason": event.reason},
            )
            self._send_alert(alert)

    def _handle_tool_failed(self, event: ToolInvocationFailed) -> None:
        """Handle tool invocation failed event."""
        alert = Alert(
            level="warning",
            message=f"Tool invocation failed: {event.tool_name}",
            provider_id=event.provider_id,
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
                provider_id=event.provider_id,
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
            except Exception as e:
                logger.error(f"Failed to send alert to sink: {e}")

    @property
    def alerts_sent(self) -> list[Alert]:
        """Get list of alerts sent (for testing)."""
        return list(self._alerts_sent)

    def clear_alerts(self) -> None:
        """Clear sent alerts (for testing)."""
        self._alerts_sent.clear()
