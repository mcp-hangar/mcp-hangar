"""Where per-server metric snapshots are kept for the history endpoints.

A port for the same reason the saga store has one: it is state the gateway
persists, so the selected storage backend has to provide it.

`MetricPoint` lives here rather than in the SQLite adapter it was born in. It is
the record the port speaks in, so both backends and every caller need it, and a
port that reaches into an adapter for its own vocabulary is the hexagon
inverted -- the import contract said so out loud. The SQLite module re-exports
the name, so existing imports keep working.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class MetricPoint:
    """A single time-series data point.

    Attributes:
        mcp_server_id: McpServer this metric belongs to.
        metric_name: Name of the metric (e.g. ``tool_calls_total``).
        value: Numeric value.
        recorded_at: Unix timestamp (seconds since epoch) when the snapshot was taken.
    """

    mcp_server_id: str
    metric_name: str
    value: float
    recorded_at: float


class IMetricsHistoryStore(ABC):
    """Port for the metric-snapshot time series."""

    @abstractmethod
    def record_snapshot(self, points: list[MetricPoint]) -> None:
        """Append a batch of points. Called by the snapshot worker."""

    @abstractmethod
    def query(
        self,
        mcp_server_id: str | None = None,
        metric_name: str | None = None,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 1000,
    ) -> list[MetricPoint]:
        """Read points back, filtered. `None` means "no filter on this field"."""

    @abstractmethod
    def prune(self) -> int:
        """Drop points past the retention window. Returns how many were removed."""
