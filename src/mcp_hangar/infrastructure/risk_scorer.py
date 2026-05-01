"""In-memory weighted risk scoring implementation.

Computes mcp_server and session risk from scored signals with exponential time decay.
"""

from __future__ import annotations

import threading
import time
from typing import final

from ..domain.value_objects.risk import RiskScore, ScoringFactor


@final
class WeightedRiskScorer:
    """Computes risk scores from weighted factors with exponential time decay."""

    NORMALIZATION_FACTOR: float = 5.0
    SEVERITY_WEIGHTS: dict[str, float] = {
        "critical": 1.0,
        "high": 0.7,
        "medium": 0.4,
        "low": 0.1,
    }

    def __init__(self, decay_half_life_seconds: float = 3600.0) -> None:
        if decay_half_life_seconds <= 0:
            raise ValueError("decay_half_life_seconds must be positive")
        self._decay_half_life_seconds: float = decay_half_life_seconds
        self._mcp_server_signals: dict[str, list[ScoringFactor]] = {}
        self._session_signals: dict[str, list[ScoringFactor]] = {}
        self._lock: threading.Lock = threading.Lock()

    def record_signal(self, mcp_server_id: str, session_id: str, factor: ScoringFactor) -> None:
        with self._lock:
            self._mcp_server_signals.setdefault(mcp_server_id, []).append(factor)
            self._session_signals.setdefault(session_id, []).append(factor)

    def get_score(self, mcp_server_id: str) -> RiskScore:
        return self._build_score(self._get_factors(self._mcp_server_signals, mcp_server_id))

    def get_session_score(self, session_id: str) -> RiskScore:
        return self._build_score(self._get_factors(self._session_signals, session_id))

    def _get_factors(self, store: dict[str, list[ScoringFactor]], key: str) -> tuple[ScoringFactor, ...]:
        with self._lock:
            return tuple(store.get(key, ()))

    def _build_score(self, factors: tuple[ScoringFactor, ...]) -> RiskScore:
        now = time.time()
        total_weight = 0.0
        for factor in factors:
            age_seconds = max(0.0, now - factor.occurred_at)
            decayed_weight = factor.weight * (2 ** (-age_seconds / self._decay_half_life_seconds))
            total_weight += decayed_weight

        score = min(100.0, total_weight * 100.0 / self.NORMALIZATION_FACTOR)
        return RiskScore(score=score, factors=factors, computed_at=now)
