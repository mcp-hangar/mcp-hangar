"""Risk scoring contract.

Defines the interface for recording risk signals and retrieving scores.
"""

from typing import Protocol

from ..value_objects.risk import RiskScore, ScoringFactor


class IRiskScorer(Protocol):
    def record_signal(self, mcp_server_id: str, session_id: str, factor: ScoringFactor) -> None: ...

    def get_score(self, mcp_server_id: str) -> RiskScore: ...

    def get_session_score(self, session_id: str) -> RiskScore: ...


class NullRiskScorer:
    def record_signal(self, mcp_server_id: str, session_id: str, factor: ScoringFactor) -> None:
        del mcp_server_id, session_id, factor
        pass

    def get_score(self, mcp_server_id: str) -> RiskScore:
        del mcp_server_id
        return RiskScore(score=0.0)

    def get_session_score(self, session_id: str) -> RiskScore:
        del session_id
        return RiskScore(score=0.0)
