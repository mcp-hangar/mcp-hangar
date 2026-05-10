"""Risk scoring value objects.

Domain primitives for anomaly and behavioral risk computation.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringFactor:
    source: str
    weight: float
    raw_severity: str
    occurred_at: float
    detail: str = ""


@dataclass(frozen=True)
class RiskScore:
    """Aggregate risk score for an MCP server or session.

    score: float in [0.0, 100.0]. Domain scale. The OTEL emission layer
    normalizes to [0.0, 1.0] before writing ``mcp.risk.score``.
    """

    score: float
    factors: tuple[ScoringFactor, ...] = ()
    computed_at: float = 0.0
