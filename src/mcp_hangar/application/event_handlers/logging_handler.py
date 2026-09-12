"""Logging event handler - a summary of every domain event, and the full event only at DEBUG."""

import logging
from typing import Any

from mcp_hangar.domain.events import (
    DomainEvent,
    HealthCheckFailed,
    McpServerDegraded,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)
#: Asked before building the DEBUG line: structlog's stdlib logger runs every processor even when DEBUG is off.
_stdlib_logger = logging.getLogger(__name__)

#: Checked in order with ``isinstance``, so the legacy ``Provider*`` aliases keep
#: their parent's level. Anything unlisted logs at INFO.
EVENT_LOG_LEVELS: tuple[tuple[tuple[type[DomainEvent], ...], int], ...] = (
    ((McpServerDegraded, ToolInvocationFailed, HealthCheckFailed), logging.WARNING),
    ((ToolInvocationRequested, ToolInvocationCompleted), logging.DEBUG),
)

#: Summary key -> the event attributes it is read from, first non-empty wins.
_SUMMARY_SOURCES = {
    "event_id": ("event_id",),
    "mcp_server_id": ("mcp_server_id", "mcp_server"),
    "tool_name": ("tool_name", "tool"),
    "error_type": ("error_type",),
    "tenant_id": ("identity_context", "tenant_id"),
    "correlation_id": ("correlation_id",),
}


def _summary(event: DomainEvent) -> dict[str, Any]:
    """Identifiers only: never ``identity_context`` itself, arguments or free text."""
    summary: dict[str, Any] = {"event_type": type(event).__name__}
    for key, sources in _SUMMARY_SOURCES.items():
        for source in sources:
            value = getattr(event, source, None)
            if isinstance(value, dict):  # identity_context: take its tenant, nothing else
                value = value.get(key)
            if isinstance(value, str) and value:
                summary[key] = value
                break
    return summary


class LoggingEventHandler:
    """Logs a summary of each domain event, and the full event at DEBUG."""

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
        level = next((lvl for types, lvl in EVENT_LOG_LEVELS if isinstance(event, types)), logging.INFO)
        logger.log(level, "domain_event", **_summary(event))
        if _stdlib_logger.isEnabledFor(logging.DEBUG):
            logger.debug("domain_event_detail", **event.to_dict())
