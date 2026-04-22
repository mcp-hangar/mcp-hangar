"""Metrics event handler - collects metrics from domain events.

This handler bridges domain events to Prometheus metrics, ensuring
all significant state changes are observable via the /metrics endpoint.
"""

from collections import defaultdict
from dataclasses import dataclass, field
import time

from mcp_hangar.domain.events import (
    CapabilityViolationDetected,
    CircuitBreakerStateChanged,
    DomainEvent,
    EgressBlocked,
    HealthCheckFailed,
    HealthCheckPassed,
    McpServerDegraded,
    McpServerStarted,
    McpServerStateChanged,
    McpServerStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
)
from mcp_hangar import metrics as prometheus_metrics


@dataclass
class McpServerMetrics:
    """Metrics for a single mcp_server."""

    mcp_server_id: str
    total_invocations: int = 0
    successful_invocations: int = 0
    failed_invocations: int = 0
    total_duration_ms: float = 0.0
    health_checks_passed: int = 0
    health_checks_failed: int = 0
    degradation_count: int = 0
    invocation_latencies: list[float] = field(default_factory=list)

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
        self._metrics: dict[str, McpServerMetrics] = defaultdict(lambda: McpServerMetrics(""))
        self._started_at = time.time()

    def handle(self, event: DomainEvent) -> None:
        """
        Handle a domain event by updating metrics.

        Updates both in-memory metrics and Prometheus metrics for observability.

        Args:
            event: The domain event to process
        """
        if isinstance(event, McpServerStarted):
            self._handle_mcp_server_started(event)
        elif isinstance(event, McpServerStopped):
            self._handle_mcp_server_stopped(event)
        elif isinstance(event, McpServerStateChanged):
            self._handle_state_changed(event)
        elif isinstance(event, ToolInvocationCompleted):
            self._handle_tool_completed(event)
        elif isinstance(event, ToolInvocationFailed):
            self._handle_tool_failed(event)
        elif isinstance(event, HealthCheckPassed):
            self._handle_health_passed(event)
        elif isinstance(event, HealthCheckFailed):
            self._handle_health_failed(event)
        elif isinstance(event, McpServerDegraded):
            self._handle_mcp_server_degraded(event)
        elif isinstance(event, CircuitBreakerStateChanged):
            self._handle_circuit_breaker_state_changed(event)
        elif isinstance(event, CapabilityViolationDetected):
            self._handle_capability_violation(event)
        elif isinstance(event, EgressBlocked):
            self._handle_egress_blocked(event)

    def _handle_mcp_server_started(self, event: McpServerStarted) -> None:
        """Handle mcp_server started event."""
        metrics = self._metrics[event.mcp_server_id]
        metrics.mcp_server_id = event.mcp_server_id

        # Update Prometheus metrics
        prometheus_metrics.record_mcp_server_start(event.mcp_server_id, success=True)
        prometheus_metrics.update_mcp_server_state(event.mcp_server_id, "ready", mode=event.mode)

    def _handle_mcp_server_stopped(self, event: McpServerStopped) -> None:
        """Handle mcp_server stopped event."""
        # Update Prometheus metrics
        prometheus_metrics.record_mcp_server_stop(event.mcp_server_id, reason=event.reason)
        prometheus_metrics.update_mcp_server_state(event.mcp_server_id, "cold")

    def _handle_state_changed(self, event: McpServerStateChanged) -> None:
        """Handle mcp_server state changed event."""
        # Update Prometheus metrics
        prometheus_metrics.update_mcp_server_state(event.mcp_server_id, event.new_state)

    def _handle_tool_completed(self, event: ToolInvocationCompleted) -> None:
        """Handle tool invocation completed event."""
        metrics = self._metrics[event.mcp_server_id]
        metrics.total_invocations += 1
        metrics.successful_invocations += 1
        metrics.total_duration_ms += event.duration_ms
        metrics.invocation_latencies.append(event.duration_ms)

        # Keep only last 1000 latencies for memory efficiency
        if len(metrics.invocation_latencies) > 1000:
            metrics.invocation_latencies = metrics.invocation_latencies[-1000:]

        # Update Prometheus metrics
        duration_s = event.duration_ms / 1000.0
        prometheus_metrics.observe_tool_call(
            mcp_server=event.mcp_server_id,
            tool=event.tool_name,
            duration=duration_s,
            success=True,
        )

    def _handle_tool_failed(self, event: ToolInvocationFailed) -> None:
        """Handle tool invocation failed event."""
        metrics = self._metrics[event.mcp_server_id]
        metrics.total_invocations += 1
        metrics.failed_invocations += 1

        # Update Prometheus metrics
        prometheus_metrics.observe_tool_call(
            mcp_server=event.mcp_server_id,
            tool=event.tool_name,
            duration=0.0,  # Duration unknown for failures
            success=False,
            error_type=event.error_type,
        )

    def _handle_health_passed(self, event: HealthCheckPassed) -> None:
        """Handle health check passed event."""
        metrics = self._metrics[event.mcp_server_id]
        metrics.health_checks_passed += 1

        # Update Prometheus metrics
        duration_s = event.duration_ms / 1000.0
        prometheus_metrics.observe_health_check(
            mcp_server=event.mcp_server_id,
            duration=duration_s,
            healthy=True,
            consecutive_failures=0,
        )

    def _handle_health_failed(self, event: HealthCheckFailed) -> None:
        """Handle health check failed event."""
        metrics = self._metrics[event.mcp_server_id]
        metrics.health_checks_failed += 1

        # Update Prometheus metrics
        prometheus_metrics.observe_health_check(
            mcp_server=event.mcp_server_id,
            duration=0.0,  # Duration unknown for failures
            healthy=False,
            consecutive_failures=event.consecutive_failures,
        )

    def _handle_mcp_server_degraded(self, event: McpServerDegraded) -> None:
        """Handle mcp_server degraded event."""
        metrics = self._metrics[event.mcp_server_id]
        metrics.degradation_count += 1

        # Update Prometheus metrics
        prometheus_metrics.update_mcp_server_state(event.mcp_server_id, "degraded")

    def _handle_circuit_breaker_state_changed(self, event: CircuitBreakerStateChanged) -> None:
        """Handle circuit breaker state changed event."""
        prometheus_metrics.update_circuit_breaker_state(event.mcp_server_id, event.new_state)

    def _handle_capability_violation(self, event: CapabilityViolationDetected) -> None:
        """Handle capability violation detected event."""
        prometheus_metrics.record_capability_violation(
            mcp_server=event.mcp_server_id,
            violation_type=event.violation_type,
        )

    def _handle_egress_blocked(self, event: EgressBlocked) -> None:
        """Handle egress blocked event."""
        prometheus_metrics.record_capability_violation(
            mcp_server=event.mcp_server_id,
            violation_type="egress_denied",
        )

    def get_metrics(self, mcp_server_id: str) -> McpServerMetrics | None:
        """
        Get metrics for a specific mcp_server.

        Args:
            mcp_server_id: The mcp_server ID

        Returns:
            McpServerMetrics if available, None otherwise
        """
        return self._metrics.get(mcp_server_id)

    def get_all_metrics(self) -> dict[str, McpServerMetrics]:
        """
        Get metrics for all mcp_servers.

        Returns:
            Dictionary of mcp_server_id -> McpServerMetrics
        """
        return dict(self._metrics)

    def reset(self) -> None:
        """Reset all metrics (mainly for testing)."""
        self._metrics.clear()
        self._started_at = time.time()
