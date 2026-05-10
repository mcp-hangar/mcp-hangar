from unittest.mock import MagicMock, patch

from mcp_hangar.application.ports.observability import NullObservabilityAdapter
from mcp_hangar.application.services.traced_mcp_server_service import (
    TracedMcpServerService,
    _normalize_risk,
)
from mcp_hangar.domain.value_objects.risk import RiskScore
from mcp_hangar.observability.conventions import Risk


class _Scorer:
    def __init__(self, score: float) -> None:
        self._score = score

    def get_score(self, mcp_server_id: str) -> RiskScore:
        _ = mcp_server_id
        return RiskScore(score=self._score, factors=(), computed_at=0.0)


class TestNormalizeRisk:
    def test_zero_returns_zero(self) -> None:
        assert _normalize_risk(0.0) == 0.0

    def test_fifty_returns_half(self) -> None:
        assert _normalize_risk(50.0) == 0.5

    def test_hundred_returns_one(self) -> None:
        assert _normalize_risk(100.0) == 1.0

    def test_over_hundred_clamps_to_one(self) -> None:
        assert _normalize_risk(150.0) == 1.0

    def test_negative_clamps_to_zero(self) -> None:
        assert _normalize_risk(-10.0) == 0.0

    def test_nan_returns_zero(self) -> None:
        assert _normalize_risk(float("nan")) == 0.0


class TestTracedServiceRiskEmission:
    def test_risk_score_emitted_when_nonzero(self) -> None:
        mock_service = MagicMock()
        mock_service.invoke_tool.return_value = {"content": "ok"}
        svc = TracedMcpServerService(
            mcp_server_service=mock_service,
            observability=NullObservabilityAdapter(),
            risk_scorer=_Scorer(50.0),
        )

        with patch("mcp_hangar.application.services.traced_mcp_server_service.get_tracer") as mock_tracer_fn:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            mock_tracer.start_as_current_span.return_value = mock_span
            mock_tracer_fn.return_value = mock_tracer

            svc.invoke_tool("math", "add", {"a": 1})

            set_calls = {call.args[0]: call.args[1] for call in mock_span.set_attribute.call_args_list}
            assert set_calls[Risk.SCORE] == 0.5

    def test_risk_score_not_emitted_when_zero(self) -> None:
        mock_service = MagicMock()
        mock_service.invoke_tool.return_value = {"content": "ok"}
        svc = TracedMcpServerService(
            mcp_server_service=mock_service,
            observability=NullObservabilityAdapter(),
            risk_scorer=_Scorer(0.0),
        )

        with patch("mcp_hangar.application.services.traced_mcp_server_service.get_tracer") as mock_tracer_fn:
            mock_tracer = MagicMock()
            mock_span = MagicMock()
            mock_span.__enter__ = MagicMock(return_value=mock_span)
            mock_span.__exit__ = MagicMock(return_value=False)
            mock_tracer.start_as_current_span.return_value = mock_span
            mock_tracer_fn.return_value = mock_tracer

            svc.invoke_tool("math", "add", {"a": 1})

            set_calls = {call.args[0]: call.args[1] for call in mock_span.set_attribute.call_args_list}
            assert Risk.SCORE not in set_calls
