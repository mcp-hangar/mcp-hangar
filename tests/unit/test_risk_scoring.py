"""Unit tests for risk scoring."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
import importlib
from typing import Protocol, cast

import pytest

from mcp_hangar.domain.contracts.risk import NullRiskScorer
from mcp_hangar.domain.events import BehavioralDeviationDetected, DetectionRuleMatched, McpServerStateChanged
from mcp_hangar.domain.value_objects.risk import RiskScore, ScoringFactor
from mcp_hangar.infrastructure.risk_scorer import WeightedRiskScorer


class RiskScoringHandlerInstance(Protocol):
    def handle(self, event: object) -> None: ...


class RiskScoringHandlerModule(Protocol):
    RiskScoringEventHandler: Callable[[object], RiskScoringHandlerInstance]


_risk_scoring_handler_module_object: object = importlib.import_module(
    "mcp_hangar.application.event_handlers.risk_scoring_handler"
)
risk_scoring_handler_module = cast(RiskScoringHandlerModule, cast(object, _risk_scoring_handler_module_object))


class RecordingRiskScorer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, ScoringFactor]] = []

    def record_signal(self, mcp_server_id: str, session_id: str, factor: ScoringFactor) -> None:
        self.calls.append((mcp_server_id, session_id, factor))

    def get_score(self, mcp_server_id: str) -> RiskScore:
        del mcp_server_id
        return RiskScore(score=0.0)

    def get_session_score(self, session_id: str) -> RiskScore:
        del session_id
        return RiskScore(score=0.0)


class TestRiskScoreValueObjects:
    def test_risk_score_is_frozen(self) -> None:
        score = RiskScore(score=12.5)

        with pytest.raises(FrozenInstanceError):
            # setattr (not direct assignment) keeps the frozen-dataclass __setattr__
            # hook engaged so it raises; a plain `score.score = ...` would trip mypy.
            setattr(score, "score", 99.0)  # noqa: B010


class TestNullRiskScorer:
    def test_returns_zero(self) -> None:
        scorer = NullRiskScorer()

        assert scorer.get_score("server-1").score == 0.0
        assert scorer.get_session_score("session-1").score == 0.0


class TestWeightedRiskScorer:
    def test_single_signal_computes_non_zero_score(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("mcp_hangar.infrastructure.risk_scorer.time.time", lambda: 1_000.0)
        scorer = WeightedRiskScorer()
        factor = ScoringFactor("DetectionRuleMatched", 0.7, "high", 1_000.0, "rule-1")

        scorer.record_signal("server-1", "session-1", factor)

        assert scorer.get_score("server-1").score > 0.0

    def test_multiple_signals_accumulate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("mcp_hangar.infrastructure.risk_scorer.time.time", lambda: 1_000.0)
        scorer = WeightedRiskScorer()
        scorer.record_signal("server-1", "session-1", ScoringFactor("a", 0.4, "medium", 1_000.0))
        scorer.record_signal("server-1", "session-2", ScoringFactor("b", 0.7, "high", 1_000.0))

        score = scorer.get_score("server-1")

        assert round(score.score, 10) == 22.0
        assert len(score.factors) == 2

    def test_time_decay_reduces_score(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("mcp_hangar.infrastructure.risk_scorer.time.time", lambda: 7_200.0)
        scorer = WeightedRiskScorer(decay_half_life_seconds=3_600.0)
        old_factor = ScoringFactor("a", 1.0, "critical", 0.0)
        fresh_factor = ScoringFactor("a", 1.0, "critical", 7_200.0)

        scorer.record_signal("old", "session-old", old_factor)
        scorer.record_signal("fresh", "session-fresh", fresh_factor)

        assert scorer.get_score("old").score < scorer.get_score("fresh").score

    def test_critical_severity_higher_than_low(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("mcp_hangar.infrastructure.risk_scorer.time.time", lambda: 1_000.0)
        scorer = WeightedRiskScorer()
        scorer.record_signal("critical", "session-1", ScoringFactor("a", 1.0, "critical", 1_000.0))
        scorer.record_signal("low", "session-2", ScoringFactor("a", 0.1, "low", 1_000.0))

        assert scorer.get_score("critical").score > scorer.get_score("low").score

    def test_session_score_is_independent_from_mcp_server_score(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("mcp_hangar.infrastructure.risk_scorer.time.time", lambda: 1_000.0)
        scorer = WeightedRiskScorer()
        scorer.record_signal("server-1", "session-1", ScoringFactor("a", 0.7, "high", 1_000.0, "rule-1"))
        scorer.record_signal("server-1", "session-2", ScoringFactor("b", 0.7, "high", 1_000.0, "rule-2"))

        server_score = scorer.get_score("server-1")
        session_score = scorer.get_session_score("session-1")

        assert len(server_score.factors) == 2
        assert len(session_score.factors) == 1
        assert session_score.score < server_score.score


class TestRiskScoringEventHandler:
    def test_creates_factor_from_behavioral_deviation(self) -> None:
        scorer = RecordingRiskScorer()
        handler = risk_scoring_handler_module.RiskScoringEventHandler(scorer)
        event = BehavioralDeviationDetected(
            mcp_server_id="server-1",
            deviation_type="new_destination",
            observed="1.2.3.4:443/tcp",
            baseline_expected="known destinations",
            severity="medium",
        )
        event.occurred_at = 123.0

        handler.handle(event)

        assert len(scorer.calls) == 1
        mcp_server_id, session_id, factor = scorer.calls[0]
        assert mcp_server_id == "server-1"
        assert session_id == ""
        assert factor == ScoringFactor(
            source="BehavioralDeviationDetected",
            weight=0.4,
            raw_severity="medium",
            occurred_at=123.0,
            detail="new_destination",
        )

    def test_creates_factor_from_detection_rule_match(self) -> None:
        scorer = RecordingRiskScorer()
        handler = risk_scoring_handler_module.RiskScoringEventHandler(scorer)
        event = DetectionRuleMatched(
            rule_id="credential-exfiltration",
            rule_name="Credential Exfiltration",
            severity="critical",
            session_id="session-1",
            mcp_server_id="server-1",
        )
        event.occurred_at = 456.0

        handler.handle(event)

        assert len(scorer.calls) == 1
        mcp_server_id, session_id, factor = scorer.calls[0]
        assert mcp_server_id == "server-1"
        assert session_id == "session-1"
        assert factor == ScoringFactor(
            source="DetectionRuleMatched",
            weight=1.0,
            raw_severity="critical",
            occurred_at=456.0,
            detail="credential-exfiltration",
        )

    def test_ignores_unrelated_events(self) -> None:
        scorer = RecordingRiskScorer()
        handler = risk_scoring_handler_module.RiskScoringEventHandler(scorer)

        handler.handle(McpServerStateChanged(mcp_server_id="server-1", old_state="cold", new_state="ready"))

        assert scorer.calls == []
