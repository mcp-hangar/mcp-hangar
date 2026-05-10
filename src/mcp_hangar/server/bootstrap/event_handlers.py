"""Event handlers registration."""

import importlib
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
from ...application.event_handlers.cost_handler import CostAttributionEventHandler
from ...application.event_handlers.risk_scoring_handler import RiskScoringEventHandler
from ...application.ports.observability import IAuditExporter, NullAuditExporter
from ...domain.contracts.cost import NullCostAttributor
from ...domain.contracts.risk import NullRiskScorer
from ...domain.events import (
    BehavioralDeviationDetected,
    CapabilityViolationDetected,
    DetectionRuleMatched,
    McpServerStateChanged,
    ToolInvocationCompleted,
    ToolInvocationFailed,
)
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

    otlp_audit_exporter: IAuditExporter
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from ...infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter

        otlp_audit_exporter = OTLPAuditExporter()
    else:
        otlp_audit_exporter = NullAuditExporter()

    cost_attributor = getattr(runtime, "cost_attributor", None) or NullCostAttributor()

    otlp_audit_handler = OTLPAuditEventHandler(
        audit_exporter=otlp_audit_exporter,
        cost_attributor=cost_attributor,
    )
    runtime.event_bus.subscribe(ToolInvocationCompleted, otlp_audit_handler.handle)
    runtime.event_bus.subscribe(ToolInvocationFailed, otlp_audit_handler.handle)
    runtime.event_bus.subscribe(McpServerStateChanged, otlp_audit_handler.handle)

    compliance_format = os.getenv("MCP_COMPLIANCE_FORMAT", "").lower()
    if compliance_format:
        compliance_output = os.getenv("MCP_COMPLIANCE_OUTPUT")
        compliance_exporter = _create_compliance_exporter(compliance_format, compliance_output)
        if compliance_exporter is not None:
            compliance_handler = OTLPAuditEventHandler(
                audit_exporter=compliance_exporter,
                cost_attributor=cost_attributor,
            )
            runtime.event_bus.subscribe(ToolInvocationCompleted, compliance_handler.handle)
            runtime.event_bus.subscribe(ToolInvocationFailed, compliance_handler.handle)
            runtime.event_bus.subscribe(McpServerStateChanged, compliance_handler.handle)
            logger.info(
                "compliance_exporter_registered",
                format=compliance_format,
                output=compliance_output or "stderr",
            )

    detection_enforcement_handler = DetectionEnforcementHandler(
        event_bus=runtime.event_bus,
        command_bus=runtime.command_bus,
    )
    runtime.event_bus.subscribe(DetectionRuleMatched, detection_enforcement_handler.handle)

    # Cost attribution -- computes cost per tool invocation
    cost_handler = CostAttributionEventHandler(
        cost_attributor=cost_attributor,
        event_bus=runtime.event_bus,
    )
    runtime.event_bus.subscribe(ToolInvocationCompleted, cost_handler.handle)

    # Risk scoring -- aggregates behavioral signals into risk scores
    risk_scorer = getattr(runtime, "risk_scorer", None) or NullRiskScorer()
    risk_handler = RiskScoringEventHandler(risk_scorer=risk_scorer)
    runtime.event_bus.subscribe(BehavioralDeviationDetected, risk_handler.handle)
    runtime.event_bus.subscribe(DetectionRuleMatched, risk_handler.handle)
    runtime.event_bus.subscribe(CapabilityViolationDetected, risk_handler.handle)

    logger.info(
        "event_handlers_registered",
        handlers=[
            "logging",
            "metrics",
            "alert",
            "audit",
            "security",
            "otlp_audit",
            "compliance" if compliance_format else None,
            "detection_enforcement",
            "cost_attribution",
            "risk_scoring",
        ],
    )


_COMPLIANCE_FORMATS = {"cef", "leef", "jsonlines", "json-lines", "syslog"}


def _create_compliance_exporter(format_name: str, output_path: str | None) -> IAuditExporter | None:
    if format_name not in _COMPLIANCE_FORMATS:
        logger.warning("unknown_compliance_format", format=format_name, supported=sorted(_COMPLIANCE_FORMATS))
        return None

    _FORMAT_TO_CLASS = {
        "cef": "CEFExporter",
        "leef": "LEEFExporter",
        "jsonlines": "JSONLinesExporter",
        "json-lines": "JSONLinesExporter",
        "syslog": "SyslogExporter",
    }
    class_name = _FORMAT_TO_CLASS.get(format_name)
    if class_name is None:
        return None

    try:
        mod = importlib.import_module("enterprise.compliance")
        exporter_cls = getattr(mod, class_name)
        exporter: IAuditExporter = exporter_cls(output_path=output_path)
        return exporter
    except (ImportError, AttributeError):
        logger.warning(
            "compliance_exporter_unavailable",
            format=format_name,
            reason="enterprise module not installed",
        )
        return None
