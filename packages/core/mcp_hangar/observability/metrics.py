"""Extended observability metrics for MCP Hangar.

Adds metrics not covered by the base metrics module:
- Circuit breaker state
- Retry attempts
- Queue depths
- Resource usage (where available)
- Cold start detailed timing

These metrics complement mcp_hangar.metrics with observability-specific
measurements useful for dashboards and alerting.
"""

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from mcp_hangar.logging_config import get_logger
from mcp_hangar.metrics import REGISTRY, Counter, Gauge, Histogram

logger = get_logger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, rejecting requests
    HALF_OPEN = "half_open"  # Testing if recovered


@dataclass
class ColdStartTiming:
    """Detailed timing for cold start phases."""

    total_ms: float = 0.0
    process_spawn_ms: float = 0.0
    connection_ms: float = 0.0
    tool_discovery_ms: float = 0.0
    first_health_check_ms: float = 0.0


class ObservabilityMetrics:
    """Extended metrics for observability dashboards and alerts.

    Thread-safe singleton providing additional metrics beyond
    the base metrics module.
    """

    _instance: Optional["ObservabilityMetrics"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ObservabilityMetrics":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """Initialize metrics."""
        # Circuit breaker metrics
        self.circuit_breaker_state = Gauge(
            name="mcp_registry_circuit_breaker_state",
            description="Circuit breaker state (0=closed, 1=open, 2=half_open)",
            labels=["provider"],
        )

        self.circuit_breaker_failures = Counter(
            name="mcp_registry_circuit_breaker_failures_total",
            description="Total circuit breaker failures",
            labels=["provider"],
        )

        self.circuit_breaker_successes = Counter(
            name="mcp_registry_circuit_breaker_successes_total",
            description="Total circuit breaker successes after recovery",
            labels=["provider"],
        )

        # Retry metrics
        self.retry_attempts = Counter(
            name="mcp_registry_retry_attempts_total",
            description="Total retry attempts",
            labels=["provider", "tool", "attempt_number"],
        )

        self.retry_exhausted = Counter(
            name="mcp_registry_retry_exhausted_total",
            description="Total times all retries were exhausted",
            labels=["provider", "tool"],
        )

        self.retry_succeeded = Counter(
            name="mcp_registry_retry_succeeded_total",
            description="Total times retry succeeded after failure",
            labels=["provider", "tool", "attempt_number"],
        )

        # Queue metrics
        self.pending_requests = Gauge(
            name="mcp_registry_pending_requests",
            description="Number of pending requests per provider",
            labels=["provider"],
        )

        self.request_queue_time_seconds = Histogram(
            name="mcp_registry_request_queue_time_seconds",
            description="Time requests spend waiting in queue",
            labels=["provider"],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
        )

        # Cold start detailed metrics
        self.cold_start_phase_duration = Histogram(
            name="mcp_registry_cold_start_phase_duration_seconds",
            description="Duration of cold start phases",
            labels=["provider", "phase"],  # phase: spawn, connect, discover, health
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        )

        self.cold_starts_in_progress = Gauge(
            name="mcp_registry_cold_starts_in_progress",
            description="Number of cold starts currently in progress",
            labels=["provider"],
        )

        # Resource metrics (best-effort)
        self.provider_memory_bytes = Gauge(
            name="mcp_registry_provider_memory_bytes",
            description="Memory usage of provider process in bytes",
            labels=["provider"],
        )

        self.provider_cpu_percent = Gauge(
            name="mcp_registry_provider_cpu_percent",
            description="CPU usage percentage of provider process",
            labels=["provider"],
        )

        # SLI metrics
        self.availability_ratio = Gauge(
            name="mcp_registry_availability_ratio",
            description="Availability ratio (ready providers / total providers)",
        )

        self.error_budget_remaining = Gauge(
            name="mcp_registry_error_budget_remaining",
            description="Remaining error budget ratio (1.0 = full budget)",
        )

        # Saturation metrics
        self.provider_utilization = Gauge(
            name="mcp_registry_provider_utilization",
            description="Provider utilization ratio (active/capacity)",
            labels=["provider"],
        )

        # Register all with global registry
        self._register_metrics()

        logger.debug("observability_metrics_initialized")

    def _register_metrics(self) -> None:
        """Register metrics with global registry."""
        metrics = [
            self.circuit_breaker_state,
            self.circuit_breaker_failures,
            self.circuit_breaker_successes,
            self.retry_attempts,
            self.retry_exhausted,
            self.retry_succeeded,
            self.pending_requests,
            self.request_queue_time_seconds,
            self.cold_start_phase_duration,
            self.cold_starts_in_progress,
            self.provider_memory_bytes,
            self.provider_cpu_percent,
            self.availability_ratio,
            self.error_budget_remaining,
            self.provider_utilization,
        ]

        for metric in metrics:
            try:
                REGISTRY.register(metric)
            except ValueError:
                # Already registered
                pass

    # Circuit breaker methods
    def set_circuit_state(self, provider: str, state: CircuitState) -> None:
        """Update circuit breaker state."""
        state_value = {"closed": 0, "open": 1, "half_open": 2}.get(state.value, 0)
        self.circuit_breaker_state.set(state_value, provider=provider)

    def record_circuit_failure(self, provider: str) -> None:
        """Record circuit breaker failure."""
        self.circuit_breaker_failures.inc(provider=provider)

    def record_circuit_success(self, provider: str) -> None:
        """Record circuit breaker success (recovery)."""
        self.circuit_breaker_successes.inc(provider=provider)

    # Retry methods
    def record_retry_attempt(self, provider: str, tool: str, attempt: int) -> None:
        """Record a retry attempt."""
        self.retry_attempts.inc(provider=provider, tool=tool, attempt_number=str(attempt))

    def record_retry_exhausted(self, provider: str, tool: str) -> None:
        """Record when all retries are exhausted."""
        self.retry_exhausted.inc(provider=provider, tool=tool)

    def record_retry_success(self, provider: str, tool: str, attempt: int) -> None:
        """Record successful retry."""
        self.retry_succeeded.inc(provider=provider, tool=tool, attempt_number=str(attempt))

    # Queue methods
    def set_pending_requests(self, provider: str, count: int) -> None:
        """Update pending request count."""
        self.pending_requests.set(count, provider=provider)

    def observe_queue_time(self, provider: str, duration_seconds: float) -> None:
        """Record time spent in queue."""
        self.request_queue_time_seconds.observe(duration_seconds, provider=provider)

    # Cold start methods
    def record_cold_start_phase(self, provider: str, phase: str, duration_seconds: float) -> None:
        """Record duration of a cold start phase.

        Args:
            provider: Provider ID.
            phase: Phase name (spawn, connect, discover, health).
            duration_seconds: Phase duration.
        """
        self.cold_start_phase_duration.observe(duration_seconds, provider=provider, phase=phase)

    def cold_start_began(self, provider: str) -> None:
        """Mark cold start in progress."""
        self.cold_starts_in_progress.inc(provider=provider)

    def cold_start_completed(self, provider: str) -> None:
        """Mark cold start completed."""
        self.cold_starts_in_progress.dec(provider=provider)

    # Resource methods
    def update_provider_resources(
        self,
        provider: str,
        memory_bytes: int | None = None,
        cpu_percent: float | None = None,
    ) -> None:
        """Update provider resource metrics.

        Args:
            provider: Provider ID.
            memory_bytes: Memory usage in bytes.
            cpu_percent: CPU usage percentage (0-100).
        """
        if memory_bytes is not None:
            self.provider_memory_bytes.set(memory_bytes, provider=provider)
        if cpu_percent is not None:
            self.provider_cpu_percent.set(cpu_percent, provider=provider)

    # SLI methods
    def update_availability(self, ready_count: int, total_count: int) -> None:
        """Update availability ratio.

        Args:
            ready_count: Number of ready providers.
            total_count: Total number of providers.
        """
        if total_count > 0:
            ratio = ready_count / total_count
        else:
            ratio = 1.0  # No providers = 100% available (vacuous truth)
        self.availability_ratio.set(ratio)

    def update_error_budget(self, remaining_ratio: float) -> None:
        """Update error budget remaining.

        Args:
            remaining_ratio: Ratio of error budget remaining (0.0 - 1.0).
        """
        self.error_budget_remaining.set(max(0.0, min(1.0, remaining_ratio)))

    def update_utilization(self, provider: str, ratio: float) -> None:
        """Update provider utilization.

        Args:
            provider: Provider ID.
            ratio: Utilization ratio (0.0 - 1.0).
        """
        self.provider_utilization.set(ratio, provider=provider)


# Singleton accessor
_metrics_instance: ObservabilityMetrics | None = None


def get_observability_metrics() -> ObservabilityMetrics:
    """Get the observability metrics singleton.

    Returns:
        ObservabilityMetrics instance.
    """
    global _metrics_instance
    if _metrics_instance is None:
        _metrics_instance = ObservabilityMetrics()
    return _metrics_instance


def reset_observability_metrics() -> None:
    """Reset metrics singleton (for testing)."""
    global _metrics_instance
    _metrics_instance = None
    ObservabilityMetrics._instance = None
