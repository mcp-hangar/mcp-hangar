"""Metrics event handler - collects metrics from domain events."""

from collections import defaultdict
from dataclasses import dataclass, field
import time
from typing import Dict, List

from mcp_hangar.domain.events import (
    DomainEvent,
    HealthCheckFailed,
    HealthCheckPassed,
    ProviderDegraded,
    ProviderStarted,
    ToolInvocationCompleted,
    ToolInvocationFailed,
)


@dataclass
class ProviderMetrics:
    """Metrics for a single provider."""

    provider_id: str
    total_invocations: int = 0
    successful_invocations: int = 0
    failed_invocations: int = 0
    total_duration_ms: float = 0.0
    health_checks_passed: int = 0
    health_checks_failed: int = 0
    degradation_count: int = 0
    invocation_latencies: List[float] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_invocations == 0:
            return 100.0
        return (self.successful_invocations / self.total_invocations) * 100

    @property
    def average_latency_ms(self) -> float:
        """Calculate average latency in milliseconds."""
        if self.total_invocations == 0:
            return 0.0
        return self.total_duration_ms / self.total_invocations

    @property
    def p95_latency_ms(self) -> float:
        """Calculate p95 latency in milliseconds."""
        if not self.invocation_latencies:
            return 0.0
        sorted_latencies = sorted(self.invocation_latencies)
        index = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[index] if index < len(sorted_latencies) else sorted_latencies[-1]


class MetricsEventHandler:
    """
    Event handler that collects metrics from domain events.

    This demonstrates how events can feed into observability systems.
    In production, this might send to Prometheus, DataDog, etc.
    """

    def __init__(self):
        """Initialize the metrics handler."""
        self._metrics: Dict[str, ProviderMetrics] = defaultdict(lambda: ProviderMetrics(""))
        self._started_at = time.time()

    def handle(self, event: DomainEvent) -> None:
        """
        Handle a domain event by updating metrics.

        Args:
            event: The domain event to process
        """
        if isinstance(event, ProviderStarted):
            self._handle_provider_started(event)
        elif isinstance(event, ToolInvocationCompleted):
            self._handle_tool_completed(event)
        elif isinstance(event, ToolInvocationFailed):
            self._handle_tool_failed(event)
        elif isinstance(event, HealthCheckPassed):
            self._handle_health_passed(event)
        elif isinstance(event, HealthCheckFailed):
            self._handle_health_failed(event)
        elif isinstance(event, ProviderDegraded):
            self._handle_provider_degraded(event)

    def _handle_provider_started(self, event: ProviderStarted) -> None:
        """Handle provider started event."""
        metrics = self._metrics[event.provider_id]
        metrics.provider_id = event.provider_id

    def _handle_tool_completed(self, event: ToolInvocationCompleted) -> None:
        """Handle tool invocation completed event."""
        metrics = self._metrics[event.provider_id]
        metrics.total_invocations += 1
        metrics.successful_invocations += 1
        metrics.total_duration_ms += event.duration_ms
        metrics.invocation_latencies.append(event.duration_ms)

        # Keep only last 1000 latencies for memory efficiency
        if len(metrics.invocation_latencies) > 1000:
            metrics.invocation_latencies = metrics.invocation_latencies[-1000:]

    def _handle_tool_failed(self, event: ToolInvocationFailed) -> None:
        """Handle tool invocation failed event."""
        metrics = self._metrics[event.provider_id]
        metrics.total_invocations += 1
        metrics.failed_invocations += 1

    def _handle_health_passed(self, event: HealthCheckPassed) -> None:
        """Handle health check passed event."""
        metrics = self._metrics[event.provider_id]
        metrics.health_checks_passed += 1

    def _handle_health_failed(self, event: HealthCheckFailed) -> None:
        """Handle health check failed event."""
        metrics = self._metrics[event.provider_id]
        metrics.health_checks_failed += 1

    def _handle_provider_degraded(self, event: ProviderDegraded) -> None:
        """Handle provider degraded event."""
        metrics = self._metrics[event.provider_id]
        metrics.degradation_count += 1

    def get_metrics(self, provider_id: str) -> ProviderMetrics | None:
        """
        Get metrics for a specific provider.

        Args:
            provider_id: The provider ID

        Returns:
            ProviderMetrics if available, None otherwise
        """
        return self._metrics.get(provider_id)

    def get_all_metrics(self) -> Dict[str, ProviderMetrics]:
        """
        Get metrics for all providers.

        Returns:
            Dictionary of provider_id -> ProviderMetrics
        """
        return dict(self._metrics)

    def reset(self) -> None:
        """Reset all metrics (mainly for testing)."""
        self._metrics.clear()
        self._started_at = time.time()
