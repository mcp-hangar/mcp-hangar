"""Event handlers registration."""

import os
from typing import TYPE_CHECKING

from ...application.event_handlers import (
    DetectionEnforcementHandler,
    LoggingEventHandler,
    MetricsEventHandler,
    get_alert_handler,
    get_audit_handler,
)
from ...application.event_handlers.audit_event_handler import OTLPAuditEventHandler
from ...application.ports.observability import NullAuditExporter
from ...domain.events import DetectionRuleMatched, ProviderStateChanged, ToolInvocationCompleted, ToolInvocationFailed
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

    detection_enforcement_handler = DetectionEnforcementHandler(
        event_bus=runtime.event_bus,
        command_bus=runtime.command_bus,
    )
    runtime.event_bus.subscribe(DetectionRuleMatched, detection_enforcement_handler.handle)

    logger.info(
        "event_handlers_registered",
        handlers=[
            "logging",
            "metrics",
            "alert",
            "audit",
            "security",
            "otlp_audit",
            "detection_enforcement",
        ],
    )
