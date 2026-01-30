"""Logging event handler - logs all domain events."""

import logging

from mcp_hangar.domain.events import (
    DomainEvent,
    HealthCheckFailed,
    ProviderDegraded,
    ProviderStarted,
    ProviderStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


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
        event_type = event.__class__.__name__
        event_data = event.to_dict()
        # Remove event_type from data if present to avoid duplication
        event_data.pop("event_type", None)

        # Different events get different log levels
        if isinstance(event, ProviderDegraded | ToolInvocationFailed | HealthCheckFailed):
            logger.warning("domain_event", event_type=event_type, **event_data)
        elif isinstance(event, ProviderStarted | ProviderStopped):
            logger.info("domain_event", event_type=event_type, **event_data)
        elif isinstance(event, ToolInvocationRequested | ToolInvocationCompleted):
            logger.debug("domain_event", event_type=event_type, **event_data)
        else:
            logger.info("domain_event", event_type=event_type, **event_data)

    def _format_event(self, event: DomainEvent) -> str:
        """
        Format an event for logging (deprecated - kept for compatibility).

        Args:
            event: The event to format

        Returns:
            Formatted log message
        """
        event_type = event.__class__.__name__
        return f"[EVENT:{event_type}] {event.to_dict()}"
