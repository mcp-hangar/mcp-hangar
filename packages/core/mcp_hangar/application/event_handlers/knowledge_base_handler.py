"""Knowledge base event handler.

Persists domain events to knowledge base (PostgreSQL, SQLite, etc.).
"""

from collections.abc import Callable

from ...domain.events import (
    DomainEvent,
    ProviderStarted,
    ProviderStateChanged,
    ProviderStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
)
from ...infrastructure.async_executor import submit_async
from ...logging_config import get_logger

logger = get_logger(__name__)


class KnowledgeBaseEventHandler:
    """Event handler that persists events to knowledge base.

    Uses strategy pattern to map event types to persistence handlers,
    following Open/Closed Principle - new event types can be added
    without modifying existing code.
    """

    def __init__(self):
        """Initialize handler with event type mappings."""
        # Strategy mapping: event type -> persistence handler
        # Using Any for handlers to avoid complex generic types
        self._handlers: dict[type[DomainEvent], Callable] = {
            ProviderStateChanged: self._persist_state_change,
            ProviderStarted: self._persist_provider_started,
            ProviderStopped: self._persist_provider_stopped,
            ToolInvocationCompleted: self._persist_tool_completed,
            ToolInvocationFailed: self._persist_tool_failed,
        }

    def handle(self, event: DomainEvent) -> None:
        """Handle a domain event by persisting to knowledge base.

        Args:
            event: Domain event to persist
        """
        from ...infrastructure.knowledge_base import is_available

        if not is_available():
            return

        # Find handler for this event type
        handler = self._handlers.get(type(event))
        if handler is None:
            return

        # Submit async persistence using executor (fire-and-forget)
        submit_async(
            handler(event),
            on_error=lambda e: logger.debug("kb_persist_error", error=str(e), event_type=type(event).__name__),
        )

    async def _persist_state_change(self, event: ProviderStateChanged) -> None:
        """Persist provider state change event."""
        from ...infrastructure.knowledge_base import record_state_change

        await record_state_change(
            provider_id=event.provider_id,
            old_state=event.old_state,
            new_state=event.new_state,
            reason=None,
        )

    async def _persist_provider_started(self, event: ProviderStarted) -> None:
        """Persist provider started event as metric."""
        from ...infrastructure.knowledge_base import record_metric

        await record_metric(
            provider_id=event.provider_id,
            metric_name="startup_duration_ms",
            metric_value=event.startup_duration_ms,
            labels={"mode": event.mode, "tools_count": event.tools_count},
        )

    async def _persist_provider_stopped(self, event: ProviderStopped) -> None:
        """Persist provider stopped event."""
        from ...infrastructure.knowledge_base import record_state_change

        await record_state_change(
            provider_id=event.provider_id,
            old_state="ready",
            new_state="stopped",
            reason=event.reason,
        )

    async def _persist_tool_completed(self, event: ToolInvocationCompleted) -> None:
        """Persist successful tool invocation."""
        from ...infrastructure.knowledge_base import audit_log

        await audit_log(
            event_type="tool_completed",
            provider=event.provider_id,
            tool=event.tool_name,
            duration_ms=int(event.duration_ms),
            success=True,
        )

    async def _persist_tool_failed(self, event: ToolInvocationFailed) -> None:
        """Persist failed tool invocation."""
        from ...infrastructure.knowledge_base import audit_log

        await audit_log(
            event_type="tool_failed",
            provider=event.provider_id,
            tool=event.tool_name,
            duration_ms=int(event.duration_ms) if hasattr(event, "duration_ms") else None,
            success=False,
            error_message=event.error[:500] if hasattr(event, "error") else None,
        )
