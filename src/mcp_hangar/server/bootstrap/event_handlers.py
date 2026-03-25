"""Event handlers registration."""

import os
from typing import TYPE_CHECKING

from ...application.event_handlers import (
    LoggingEventHandler,
    MetricsEventHandler,
    get_alert_handler,
    get_audit_handler,
)
from ...application.event_handlers.audit_event_handler import OTLPAuditEventHandler
from ...application.ports.observability import NullAuditExporter
from ...domain.events import ProviderStateChanged, ToolInvocationCompleted, ToolInvocationFailed
from ...logging_config import get_logger

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

logger = get_logger(__name__)


def init_event_handlers(runtime: "Runtime") -> None:
    """Register all event handlers.

    Args:
        runtime: Runtime instance with event bus.
    """
    logging_handler = LoggingEventHandler()
    runtime.event_bus.subscribe_to_all(logging_handler.handle)

    metrics_handler = MetricsEventHandler()
    runtime.event_bus.subscribe_to_all(metrics_handler.handle)

    alert_handler = get_alert_handler()
    runtime.event_bus.subscribe_to_all(alert_handler.handle)

    audit_handler = get_audit_handler()
    runtime.event_bus.subscribe_to_all(audit_handler.handle)

    runtime.event_bus.subscribe_to_all(runtime.security_handler.handle)

    # OTLP audit exporter handler -- exports security events as OTLP log records
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from ...infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter

        otlp_audit_exporter = OTLPAuditExporter()
    else:
        otlp_audit_exporter = NullAuditExporter()

    otlp_audit_handler = OTLPAuditEventHandler(audit_exporter=otlp_audit_exporter)
    runtime.event_bus.subscribe(ToolInvocationCompleted, otlp_audit_handler.handle)
    runtime.event_bus.subscribe(ToolInvocationFailed, otlp_audit_handler.handle)
    runtime.event_bus.subscribe(ProviderStateChanged, otlp_audit_handler.handle)

    # Behavioral deviation handler (OTLP spans + Prometheus counter)
    from ...application.event_handlers.behavioral_deviation_handler import (
        BehavioralDeviationEventHandler,
    )
    from ...domain.events import BehavioralDeviationDetected

    behavioral_deviation_handler = BehavioralDeviationEventHandler()
    runtime.event_bus.subscribe(BehavioralDeviationDetected, behavioral_deviation_handler.handle)

    # Knowledge base handler (PostgreSQL persistence)
    from ...application.event_handlers.knowledge_base_handler import KnowledgeBaseEventHandler
    from ...infrastructure.async_executor import async_executor

    kb_handler = KnowledgeBaseEventHandler(async_task=async_executor)
    runtime.event_bus.subscribe_to_all(kb_handler.handle)

    logger.info(
        "event_handlers_registered",
        handlers=[
            "logging",
            "metrics",
            "alert",
            "audit",
            "security",
            "otlp_audit",
            "behavioral_deviation",
            "knowledge_base",
        ],
    )
