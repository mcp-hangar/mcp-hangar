"""Prometheus Metrics Publisher - Infrastructure implementation.

This adapter implements the IMetricsPublisher contract using Prometheus metrics.
"""

from ..domain.contracts.metrics_publisher import IMetricsPublisher


class PrometheusMetricsPublisher(IMetricsPublisher):
    """Prometheus implementation of metrics publisher."""

    def __init__(self):
        """Initialize with lazy import to avoid circular dependencies."""
        self._metrics = None

    def _ensure_metrics(self):
        """Lazy load metrics module."""
        if self._metrics is None:
            from mcp_hangar import metrics

            self._metrics = metrics

    def record_cold_start(self, provider_id: str, duration_s: float, mode: str) -> None:
        """Record a cold start event."""
        self._ensure_metrics()
        self._metrics.record_cold_start(provider_id, duration_s, mode)

    def begin_cold_start(self, provider_id: str) -> None:
        """Mark the beginning of a cold start."""
        self._ensure_metrics()
        self._metrics.cold_start_begin(provider_id)

    def end_cold_start(self, provider_id: str) -> None:
        """Mark the end of a cold start."""
        self._ensure_metrics()
        self._metrics.cold_start_end(provider_id)
