"""Logging event handler - logs all domain events."""

import json
import logging

from mcp_hangar.domain.events import (
    DomainEvent,
    HealthCheckFailed,
    HealthCheckPassed,
    ProviderDegraded,
    ProviderIdleDetected,
    ProviderStarted,
    ProviderStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)

logger = logging.getLogger(__name__)


class LoggingEventHandler:
    """
    Event handler that logs all domain events in structured format.

    This demonstrates the event-driven pattern and provides audit trail.
    """

    def __init__(self, log_level: int = logging.INFO):
        """
        Initialize the logging handler.

        Args:
            log_level: Logging level for events (default: INFO)
        """
        self.log_level = log_level

    def handle(self, event: DomainEvent) -> None:
        """
        Handle a domain event by logging it.

        Args:
            event: The domain event to log
        """
        # Different events get different log levels
        if isinstance(event, (ProviderDegraded, ToolInvocationFailed, HealthCheckFailed)):
            level = logging.WARNING
        elif isinstance(event, (ProviderStarted, ProviderStopped)):
            level = logging.INFO
        elif isinstance(event, (ToolInvocationRequested, ToolInvocationCompleted)):
            level = logging.DEBUG
        else:
            level = self.log_level

        # Log with structured data
        logger.log(level, self._format_event(event))

    def _format_event(self, event: DomainEvent) -> str:
        """
        Format an event for logging.

        Args:
            event: The event to format

        Returns:
            Formatted log message
        """
        event_type = event.__class__.__name__
        event_data = event.to_dict()

        # Create structured log message
        if isinstance(event, ProviderStarted):
            return (
                f"[EVENT:{event_type}] Provider '{event.provider_id}' started "
                f"in {event.startup_duration_ms:.2f}ms with {event.tools_count} tools"
            )
        elif isinstance(event, ProviderStopped):
            return f"[EVENT:{event_type}] Provider '{event.provider_id}' stopped " f"(reason: {event.reason})"
        elif isinstance(event, ProviderDegraded):
            return (
                f"[EVENT:{event_type}] Provider '{event.provider_id}' DEGRADED "
                f"after {event.consecutive_failures} consecutive failures "
                f"(total: {event.total_failures})"
            )
        elif isinstance(event, ToolInvocationRequested):
            return (
                f"[EVENT:{event_type}] Tool '{event.tool_name}' invocation requested "
                f"on '{event.provider_id}' [correlation_id={event.correlation_id}]"
            )
        elif isinstance(event, ToolInvocationCompleted):
            return (
                f"[EVENT:{event_type}] Tool '{event.tool_name}' completed "
                f"in {event.duration_ms:.2f}ms [correlation_id={event.correlation_id}]"
            )
        elif isinstance(event, ToolInvocationFailed):
            return (
                f"[EVENT:{event_type}] Tool '{event.tool_name}' FAILED: {event.error_message} "
                f"[correlation_id={event.correlation_id}]"
            )
        elif isinstance(event, HealthCheckPassed):
            return (
                f"[EVENT:{event_type}] Provider '{event.provider_id}' health check passed "
                f"in {event.duration_ms:.2f}ms"
            )
        elif isinstance(event, HealthCheckFailed):
            return (
                f"[EVENT:{event_type}] Provider '{event.provider_id}' health check FAILED "
                f"(consecutive: {event.consecutive_failures}): {event.error_message}"
            )
        elif isinstance(event, ProviderIdleDetected):
            return f"[EVENT:{event_type}] Provider '{event.provider_id}' idle for " f"{event.idle_duration_s:.1f}s"
        else:
            # Generic format
            return f"[EVENT:{event_type}] {json.dumps(event_data)}"
