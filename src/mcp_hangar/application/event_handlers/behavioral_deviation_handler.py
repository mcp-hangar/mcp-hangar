"""Event handler bridging behavioral deviations to OTLP spans and Prometheus metrics.

Listens for BehavioralDeviationDetected domain events and:
1. Increments the ``mcp_hangar_behavioral_deviations_total`` Prometheus counter.
2. Creates an OTLP span with governance attributes for each deviation.

This handler lives in the MIT application layer because it only depends on
domain events and core observability conventions. The enterprise profiler
publishes the events; this handler consumes them.
"""

import structlog
from opentelemetry import trace

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.domain.events import BehavioralDeviationDetected, DomainEvent
from mcp_hangar.observability.conventions import Behavioral, Enforcement, Provider

logger = structlog.get_logger(__name__)


class BehavioralDeviationEventHandler:
    """Bridges BehavioralDeviationDetected events to OTLP and Prometheus.

    For each deviation event:
    - Increments ``mcp_hangar_behavioral_deviations_total`` counter with
      provider and deviation_type labels.
    - Creates an OTLP span named ``behavioral.deviation`` with governance
      attributes: provider ID, violation type, deviation type, severity.
    """

    def handle(self, event: DomainEvent) -> None:
        """Handle a domain event if it is a BehavioralDeviationDetected.

        Non-matching events are silently ignored.

        Args:
            event: The domain event to process.
        """
        if not isinstance(event, BehavioralDeviationDetected):
            return

        # Increment Prometheus counter
        prometheus_metrics.record_behavioral_deviation(
            provider=event.provider_id,
            deviation_type=event.deviation_type,
        )

        # Create OTLP span with governance attributes
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("behavioral.deviation") as span:
            span.set_attribute(Provider.ID, event.provider_id)
            span.set_attribute(Enforcement.VIOLATION_TYPE, "behavioral_deviation")
            span.set_attribute(Behavioral.DEVIATION_TYPE, event.deviation_type)
            span.set_attribute(Enforcement.VIOLATION_SEVERITY, event.severity)

        logger.debug(
            "behavioral_deviation_event_handled",
            provider_id=event.provider_id,
            deviation_type=event.deviation_type,
            severity=event.severity,
        )
