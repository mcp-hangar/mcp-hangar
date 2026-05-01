"""Risk scoring event handler.

Subscribes to behavioral and detection events and records scored risk signals.
"""

from typing import final

from ...domain.contracts.risk import IRiskScorer
from ...domain.events import BehavioralDeviationDetected, CapabilityViolationDetected, DetectionRuleMatched
from ...domain.value_objects.risk import ScoringFactor

SEVERITY_WEIGHTS = {
    "critical": 1.0,
    "high": 0.7,
    "medium": 0.4,
    "low": 0.1,
}


@final
class RiskScoringEventHandler:
    """Converts security-related events into risk scoring factors."""

    def __init__(self, risk_scorer: IRiskScorer) -> None:
        self._risk_scorer: IRiskScorer = risk_scorer

    def handle(self, event: object) -> None:
        if isinstance(event, BehavioralDeviationDetected):
            factor = ScoringFactor(
                source="BehavioralDeviationDetected",
                weight=SEVERITY_WEIGHTS[event.severity],
                raw_severity=event.severity,
                occurred_at=event.occurred_at,
                detail=event.deviation_type,
            )
            self._risk_scorer.record_signal(event.mcp_server_id, "", factor)
            return

        if isinstance(event, DetectionRuleMatched):
            factor = ScoringFactor(
                source="DetectionRuleMatched",
                weight=SEVERITY_WEIGHTS[event.severity],
                raw_severity=event.severity,
                occurred_at=event.occurred_at,
                detail=event.rule_id,
            )
            self._risk_scorer.record_signal(event.mcp_server_id, event.session_id, factor)
            return

        if isinstance(event, CapabilityViolationDetected):
            factor = ScoringFactor(
                source="CapabilityViolationDetected",
                weight=0.7,
                raw_severity="high",
                occurred_at=event.occurred_at,
                detail=str(event.violation_type),
            )
            self._risk_scorer.record_signal(event.mcp_server_id, "", factor)
