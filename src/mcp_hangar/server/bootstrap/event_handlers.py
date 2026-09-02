"""Event handlers registration."""

import importlib
import os
from typing import TYPE_CHECKING

from ..api.sessions import get_session_suspension_registry
from ...application.event_handlers import (
    DetectionEnforcementHandler,
    LoggingEventHandler,
    get_alert_handler,
    get_audit_handler,
)
from ...infrastructure.observability.metrics_event_handler import MetricsEventHandler
from ...application.event_handlers.audit_event_handler import OTLPAuditEventHandler
from ...application.event_handlers.cost_handler import CostAttributionEventHandler
from ...application.event_handlers.risk_scoring_handler import RiskScoringEventHandler
from ...application.ports.observability import IAuditExporter, NullAuditExporter
from ...domain.contracts.cost import NullCostAttributor
from ...domain.contracts.risk import NullRiskScorer
from ...application.event_handlers.tool_projection_handler import ToolProjectionPopulationHandler
from ...domain.events import (
    McpServerDeregistered,
    McpServerRegistered,
    SessionSuspended,
    SessionUnsuspended,
    BehavioralDeviationDetected,
    CapabilityViolationDetected,
    DetectionRuleMatched,
    McpServerStarted,
    McpServerStateChanged,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolRestored,
    ToolWithdrawn,
)
from ...domain.contracts.event_bus import HandlerKind
from ...logging_config import get_logger

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

logger = get_logger(__name__)


def init_event_handlers(runtime: "Runtime") -> None:
    """Register all event handlers.

    Args:
        runtime: Runtime instance with event bus.
    """
    # Every subscription below states its kind, and the four subscribe-to-all
    # handlers are where getting it wrong multiplies by the number of replicas.
    # They are effects: this replica's log lines, this replica's Prometheus
    # counters (three replicas each counting every event would triple every
    # total when the scrapes are summed), alerts, and the audit trail.
    logging_handler = LoggingEventHandler()
    runtime.event_bus.subscribe_to_all(logging_handler.handle, kind=HandlerKind.EFFECT)

    metrics_handler = MetricsEventHandler()
    runtime.event_bus.subscribe_to_all(metrics_handler.handle, kind=HandlerKind.EFFECT)

    alert_handler = get_alert_handler()
    runtime.event_bus.subscribe_to_all(alert_handler.handle, kind=HandlerKind.EFFECT)

    audit_handler = get_audit_handler()
    runtime.event_bus.subscribe_to_all(audit_handler.handle, kind=HandlerKind.EFFECT)

    runtime.event_bus.subscribe_to_all(runtime.security_handler.handle, kind=HandlerKind.EFFECT)

    # Populate the tool-projection registry from discovered tools on server start (#248)
    #
    # LOCAL_VIEW, not PROJECTION: the handler reads the local aggregate, not the
    # event, so a peer's tailed start had it rebuild from nothing and delete a
    # catalogue this replica was serving. See `HandlerKind.LOCAL_VIEW` and #922.
    # The reason it was a projection -- no replica may serve a third of the
    # catalogue -- is answered by every replica starting the fleet itself (#885).
    tool_projection_handler = ToolProjectionPopulationHandler(repository=runtime.repository)
    runtime.event_bus.subscribe(McpServerStarted, tool_projection_handler.handle, kind=HandlerKind.LOCAL_VIEW)

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
    # Exports spans and audit records outward. One invocation, one export.
    runtime.event_bus.subscribe(ToolInvocationCompleted, otlp_audit_handler.handle, kind=HandlerKind.EFFECT)
    runtime.event_bus.subscribe(ToolInvocationFailed, otlp_audit_handler.handle, kind=HandlerKind.EFFECT)
    runtime.event_bus.subscribe(McpServerStateChanged, otlp_audit_handler.handle, kind=HandlerKind.EFFECT)

    compliance_format = os.getenv("MCP_COMPLIANCE_FORMAT", "").lower()
    if compliance_format:
        compliance_output = os.getenv("MCP_COMPLIANCE_OUTPUT")
        compliance_exporter = _create_compliance_exporter(compliance_format, compliance_output)
        if compliance_exporter is not None:
            compliance_handler = OTLPAuditEventHandler(
                audit_exporter=compliance_exporter,
                cost_attributor=cost_attributor,
            )
            # The SIEM feed. The reason this taxonomy exists at all: without
            # it, three replicas send three CEF records for one tool call.
            runtime.event_bus.subscribe(ToolInvocationCompleted, compliance_handler.handle, kind=HandlerKind.EFFECT)
            runtime.event_bus.subscribe(ToolInvocationFailed, compliance_handler.handle, kind=HandlerKind.EFFECT)
            runtime.event_bus.subscribe(McpServerStateChanged, compliance_handler.handle, kind=HandlerKind.EFFECT)
            logger.info(
                "compliance_exporter_registered",
                format=compliance_format,
                output=compliance_output or "stderr",
            )

    detection_enforcement_handler = DetectionEnforcementHandler(
        event_bus=runtime.event_bus,
        command_bus=runtime.command_bus,
        # The SAME instance the HTTP suspend/unsuspend routes use. Two instances
        # would silently disagree: a session suspended by a detection rule would
        # stay servable, and one suspended over HTTP would be invisible here.
        session_registry=get_session_suspension_registry(),
    )
    # An effect: it suspends sessions, stops servers and emits
    # `EnforcementActionTaken`. Classifying it as a projection would have every
    # replica take the action and emit the event, which multiplies both.
    #
    # Known limit, and it is a security one: the suspension it applies is local,
    # so the same session reaching a different replica is not suspended. Closing
    # that means making the suspension *registry* shared rather than making this
    # handler run everywhere -- #790, phase 3.2.
    runtime.event_bus.subscribe(DetectionRuleMatched, detection_enforcement_handler.handle, kind=HandlerKind.EFFECT)

    # One fleet, seen from every replica. A server registered on one of them --
    # by an operator, or by whichever is running discovery -- was invisible to
    # the others until they restarted (#790, phase 2.3). A projection: every
    # replica needs it, for every event, whoever produced it.
    #
    # Only with a durable shared record, because the event names the server and
    # the record describes it. Without one there is nothing to read, and also no
    # peers to learn from.
    config_repository = getattr(runtime, "config_repository", None)
    if config_repository is not None and type(config_repository).__name__ != "InMemoryMcpServerConfigRepository":
        from ...application.event_handlers.fleet_projection import FleetProjection

        from ...infrastructure.async_bridge import BackgroundLoop

        fleet_projection = FleetProjection(runtime.repository, config_repository, BackgroundLoop())
        runtime.event_bus.subscribe(McpServerRegistered, fleet_projection.handle, kind=HandlerKind.PROJECTION)
        runtime.event_bus.subscribe(McpServerDeregistered, fleet_projection.handle, kind=HandlerKind.PROJECTION)
        # L7 policy changes have to reach every replica the same way (#991):
        # the setting replica saved the row before publishing, peers re-read it
        # off the tailed event. Without this, exactly one replica enforced.
        from ...domain.events.enforcement import EgressPolicyCleared, EgressPolicySet

        runtime.event_bus.subscribe(EgressPolicySet, fleet_projection.handle, kind=HandlerKind.PROJECTION)
        runtime.event_bus.subscribe(EgressPolicyCleared, fleet_projection.handle, kind=HandlerKind.PROJECTION)
        # Not `fleet_projection_registered`: that name belongs to the
        # projection applying a server, and two different events under one name
        # is a log an operator cannot read. `_configured` matches
        # `fleet_writer_configured` next door.
        logger.info("fleet_projection_configured")

    # Suspension has to reach every replica: a session refused here and served
    # by the other two is a block a caller walks past by retrying. A projection,
    # so the tail applies it on peers as well as here (#790, phase 3.2).
    from ...application.event_handlers.session_suspension_projection import SessionSuspensionProjection
    from ..api.sessions import get_session_suspension_registry as _session_registry

    suspension_projection = SessionSuspensionProjection(_session_registry())
    runtime.event_bus.subscribe(SessionSuspended, suspension_projection.handle, kind=HandlerKind.PROJECTION)
    runtime.event_bus.subscribe(SessionUnsuspended, suspension_projection.handle, kind=HandlerKind.PROJECTION)

    # A withdrawal has to reach every replica for the same reason a suspension
    # does: withdrawn here and served by the other two is a control a caller
    # walks past by retrying (#1165). The startup half of it -- rebuilding the
    # overlay a restart would otherwise drop -- is `restore_runtime_withdrawals`.
    from ...application.event_handlers.withdrawal_projection import WithdrawalProjection
    from ...application.read_models.tool_projection import get_tool_projection_registry

    withdrawal_projection = WithdrawalProjection(get_tool_projection_registry())
    runtime.event_bus.subscribe(ToolWithdrawn, withdrawal_projection.handle, kind=HandlerKind.PROJECTION)
    runtime.event_bus.subscribe(ToolRestored, withdrawal_projection.handle, kind=HandlerKind.PROJECTION)

    # Cost attribution -- computes cost per tool invocation
    cost_handler = CostAttributionEventHandler(
        cost_attributor=cost_attributor,
        event_bus=runtime.event_bus,
    )
    # Charges for work done here, and publishes `CostReportGenerated`. A
    # handler that publishes cannot be a projection: the event it raises while
    # applying a tailed event would itself be tailed, on every replica.
    runtime.event_bus.subscribe(ToolInvocationCompleted, cost_handler.handle, kind=HandlerKind.EFFECT)

    # Risk scoring -- aggregates behavioral signals into risk scores
    risk_scorer = getattr(runtime, "risk_scorer", None) or NullRiskScorer()
    risk_handler = RiskScoringEventHandler(risk_scorer=risk_scorer)
    # A projection: it records scored signals and publishes nothing. A risk
    # score assembled from one replica's share of the signals is not a risk
    # score, and the decisions taken on it would differ per replica.
    runtime.event_bus.subscribe(BehavioralDeviationDetected, risk_handler.handle, kind=HandlerKind.PROJECTION)
    runtime.event_bus.subscribe(DetectionRuleMatched, risk_handler.handle, kind=HandlerKind.PROJECTION)
    runtime.event_bus.subscribe(CapabilityViolationDetected, risk_handler.handle, kind=HandlerKind.PROJECTION)

    logger.info(
        "event_handlers_registered",
        handlers=[
            "logging",
            "metrics",
            "alert",
            "audit",
            "security",
            "tool_projection_population",
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
        mod = importlib.import_module("mcp_hangar.compliance")
        exporter_cls = getattr(mod, class_name)
        exporter: IAuditExporter = exporter_cls(output_path=output_path)
        return exporter
    except (ImportError, AttributeError):
        logger.warning(
            "compliance_exporter_unavailable",
            format=format_name,
            reason="compliance module not installed",
        )
        return None
