"""Where per-server metric snapshots are kept for the history endpoints.

A port for the same reason the saga store has one: it is state the gateway
persists, so the selected storage backend has to provide it. `MetricPoint`
stays where it is defined and is imported here rather than redeclared -- a
second definition of the same record is how two halves of a system stop
agreeing about what a metric is.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_hangar.infrastructure.persistence.metrics_history_store import MetricPoint


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
