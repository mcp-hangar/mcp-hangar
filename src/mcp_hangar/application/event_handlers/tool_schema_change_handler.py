"""Event handler bridging ProviderStarted events to tool schema drift detection.

Listens for ProviderStarted domain events and triggers schema comparison
via the SchemaTracker (enterprise). For each detected change (added,
removed, modified), publishes a ToolSchemaChanged domain event, increments
the Prometheus counter, and creates an OTLP span.

This handler lives in the MIT application layer. It receives the
SchemaTracker via dependency injection at bootstrap time. When
SchemaTracker is None (enterprise not available), the handler is a no-op.
"""

from typing import Any

import structlog
from opentelemetry import trace

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.domain.events import DomainEvent, ProviderStarted, ToolSchemaChanged
from mcp_hangar.observability.conventions import (
    Behavioral,
    Enforcement,
    MCP,
    Provider as ProviderConv,
)

logger = structlog.get_logger(__name__)


class ToolSchemaChangeHandler:
    """Bridges ProviderStarted events to schema drift detection.

    On each ProviderStarted event:
    1. Reads tool schemas from the provider via get_tool_schemas()
    2. Calls SchemaTracker.check_and_store()
    3. Publishes ToolSchemaChanged events for each detected change
    4. Increments mcp_hangar_tool_schema_drifts_total counter
    5. Creates OTLP span for each change

    When schema_tracker is None (enterprise not installed), all events
    are silently ignored.

    Args:
        schema_tracker: Enterprise SchemaTracker instance (or None).
        providers: Reference to PROVIDERS dict for accessing providers.
        event_bus: EventBus for publishing ToolSchemaChanged events.
    """

    def __init__(
        self,
        schema_tracker: Any,
        providers: Any,
        event_bus: Any,
    ) -> None:
        self._schema_tracker = schema_tracker
        self._providers = providers
        self._event_bus = event_bus

    def handle(self, event: DomainEvent) -> None:
        """Handle a domain event if it is a ProviderStarted.

        Non-matching events are silently ignored. SchemaTracker errors
        are logged but do not propagate (fault isolation).

        Args:
            event: The domain event to process.
        """
        if not isinstance(event, ProviderStarted):
            return
        if self._schema_tracker is None:
            return

        provider = self._providers.get(event.provider_id)
        if provider is None:
            logger.warning(
                "schema_check_provider_not_found",
                provider_id=event.provider_id,
            )
            return

        try:
            tools = provider.get_tool_schemas()
            changes = self._schema_tracker.check_and_store(event.provider_id, tools)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: schema check must not crash event pipeline
            logger.error(
                "schema_check_failed",
                provider_id=event.provider_id,
                error=str(e),
            )
            return

        tracer = trace.get_tracer(__name__)
        for change in changes:
            # Publish domain event
            schema_event = ToolSchemaChanged(
                provider_id=event.provider_id,
                tool_name=change["tool_name"],
                change_type=change["change_type"],
                old_hash=change.get("old_hash"),
                new_hash=change.get("new_hash"),
            )
            self._event_bus.publish(schema_event)

            # Increment Prometheus counter
            prometheus_metrics.record_tool_schema_drift(
                provider=event.provider_id,
                change_type=change["change_type"],
            )

            # Create OTLP span with governance attributes
            with tracer.start_as_current_span("schema.drift") as span:
                span.set_attribute(ProviderConv.ID, event.provider_id)
                span.set_attribute(Enforcement.VIOLATION_TYPE, "tool_schema_drift")
                span.set_attribute(Behavioral.DEVIATION_TYPE, "schema_drift")
                span.set_attribute(MCP.TOOL_NAME, change["tool_name"])
                span.set_attribute("mcp.schema.change_type", change["change_type"])

        if changes:
            logger.info(
                "tool_schema_drift_events_published",
                provider_id=event.provider_id,
                changes_count=len(changes),
            )
