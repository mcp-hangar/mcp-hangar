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
    score: float
    factors: tuple[ScoringFactor, ...] = ()
    computed_at: float = 0.0
