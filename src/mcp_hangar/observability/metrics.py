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

from enum import Enum
import threading

from mcp_hangar.logging_config import get_logger
from mcp_hangar.metrics import Counter, Gauge, Histogram, REGISTRY

logger = get_logger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, rejecting requests
    HALF_OPEN = "half_open"  # Testing if recovered


class ObservabilityMetrics:
    """Extended metrics for observability dashboards and alerts.

    Thread-safe singleton providing additional metrics beyond
    the base metrics module.
    """

    _instance: "ObservabilityMetrics | None" = None
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
            name="mcp_hangar_circuit_breaker_state",
            description="Circuit breaker state (0=closed, 1=open, 2=half_open)",
            labels=["mcp_server"],
        )

        self.circuit_breaker_failures = Counter(
            name="mcp_hangar_circuit_breaker_failures_total",
            description="Total circuit breaker failures",
            labels=["mcp_server"],
        )

        self.circuit_breaker_successes = Counter(
            name="mcp_hangar_circuit_breaker_successes_total",
            description="Total circuit breaker successes after recovery",
            labels=["mcp_server"],
        )

        # Retry metrics
        self.retry_attempts = Counter(
            name="mcp_hangar_retry_attempts_total",
            description="Total retry attempts",
            labels=["mcp_server", "tool", "attempt_number"],
        )

        self.retry_exhausted = Counter(
            name="mcp_hangar_retry_exhausted_total",
            description="Total times all retries were exhausted",
            labels=["mcp_server", "tool"],
        )

        self.retry_succeeded = Counter(
            name="mcp_hangar_retry_succeeded_total",
            description="Total times retry succeeded after failure",
            labels=["mcp_server", "tool", "attempt_number"],
        )

        # Queue metrics
        self.pending_requests = Gauge(
            name="mcp_hangar_pending_requests",
            description="Number of pending requests per mcp_server",
            labels=["mcp_server"],
        )

        self.request_queue_time_seconds = Histogram(
            name="mcp_hangar_request_queue_time_seconds",
            description="Time requests spend waiting in queue",
            labels=["mcp_server"],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
        )

        # Cold start detailed metrics
        self.cold_start_phase_duration = Histogram(
            name="mcp_hangar_cold_start_phase_duration_seconds",
            description="Duration of cold start phases",
            labels=["mcp_server", "phase"],  # phase: spawn, connect, discover, health
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        )

        self.cold_starts_in_progress = Gauge(
            name="mcp_hangar_cold_starts_in_progress",
            description="Number of cold starts currently in progress",
            labels=["mcp_server"],
        )

        # Resource metrics (best-effort)
        self.mcp_server_memory_bytes = Gauge(
            name="mcp_hangar_mcp_server_memory_bytes",
            description="Memory usage of mcp_server process in bytes",
            labels=["mcp_server"],
        )

        self.mcp_server_cpu_percent = Gauge(
            name="mcp_hangar_mcp_server_cpu_percent",
            description="CPU usage percentage of mcp_server process",
            labels=["mcp_server"],
        )

        # SLI metrics
        self.availability_ratio = Gauge(
            name="mcp_hangar_availability_ratio",
            description="Availability ratio (ready mcp_servers / total mcp_servers)",
        )

        self.error_budget_remaining = Gauge(
            name="mcp_hangar_error_budget_remaining",
            description="Remaining error budget ratio (1.0 = full budget)",
        )

        # Saturation metrics
        self.mcp_server_utilization = Gauge(
            name="mcp_hangar_mcp_server_utilization",
            description="McpServer utilization ratio (active/capacity)",
            labels=["mcp_server"],
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
            self.mcp_server_memory_bytes,
            self.mcp_server_cpu_percent,
            self.availability_ratio,
            self.error_budget_remaining,
            self.mcp_server_utilization,
        ]

        for metric in metrics:
            try:
                REGISTRY.register(metric)
            except ValueError:
                # Already registered
                pass

    # Circuit breaker methods
    def set_circuit_state(self, mcp_server: str, state: CircuitState) -> None:
        """Update circuit breaker state."""
        state_value = {"closed": 0, "open": 1, "half_open": 2}.get(state.value, 0)
        self.circuit_breaker_state.set(state_value, mcp_server=mcp_server)

    def record_circuit_failure(self, mcp_server: str) -> None:
        """Record circuit breaker failure."""
        self.circuit_breaker_failures.inc(mcp_server=mcp_server)

    def record_circuit_success(self, mcp_server: str) -> None:
        """Record circuit breaker success (recovery)."""
        self.circuit_breaker_successes.inc(mcp_server=mcp_server)

    # Retry methods
    def record_retry_attempt(self, mcp_server: str, tool: str, attempt: int) -> None:
        """Record a retry attempt."""
        self.retry_attempts.inc(mcp_server=mcp_server, tool=tool, attempt_number=str(attempt))

    def record_retry_exhausted(self, mcp_server: str, tool: str) -> None:
        """Record when all retries are exhausted."""
        self.retry_exhausted.inc(mcp_server=mcp_server, tool=tool)

    def record_retry_success(self, mcp_server: str, tool: str, attempt: int) -> None:
        """Record successful retry."""
        self.retry_succeeded.inc(mcp_server=mcp_server, tool=tool, attempt_number=str(attempt))

    # Queue methods
    def set_pending_requests(self, mcp_server: str, count: int) -> None:
        """Update pending request count."""
        self.pending_requests.set(count, mcp_server=mcp_server)

    def observe_queue_time(self, mcp_server: str, duration_seconds: float) -> None:
        """Record time spent in queue."""
        self.request_queue_time_seconds.observe(duration_seconds, mcp_server=mcp_server)

    # Cold start methods
    def record_cold_start_phase(self, mcp_server: str, phase: str, duration_seconds: float) -> None:
        """Record duration of a cold start phase.

        Args:
            mcp_server: McpServer ID.
            phase: Phase name (spawn, connect, discover, health).
            duration_seconds: Phase duration.
        """
        self.cold_start_phase_duration.observe(duration_seconds, mcp_server=mcp_server, phase=phase)

    def cold_start_began(self, mcp_server: str) -> None:
        """Mark cold start in progress."""
        self.cold_starts_in_progress.inc(mcp_server=mcp_server)

    def cold_start_completed(self, mcp_server: str) -> None:
        """Mark cold start completed."""
        self.cold_starts_in_progress.dec(mcp_server=mcp_server)

    # Resource methods
    def update_mcp_server_resources(
        self,
        mcp_server: str,
        memory_bytes: int | None = None,
        cpu_percent: float | None = None,
    ) -> None:
        """Update mcp_server resource metrics.

        Args:
            mcp_server: McpServer ID.
            memory_bytes: Memory usage in bytes.
            cpu_percent: CPU usage percentage (0-100).
        """
        if memory_bytes is not None:
            self.mcp_server_memory_bytes.set(memory_bytes, mcp_server=mcp_server)
        if cpu_percent is not None:
            self.mcp_server_cpu_percent.set(cpu_percent, mcp_server=mcp_server)

    # SLI methods
    def update_availability(self, ready_count: int, total_count: int) -> None:
        """Update availability ratio.

        Args:
            ready_count: Number of ready mcp_servers.
            total_count: Total number of mcp_servers.
        """
        if total_count > 0:
            ratio = ready_count / total_count
        else:
            ratio = 1.0  # No mcp_servers = 100% available (vacuous truth)
        self.availability_ratio.set(ratio)

    def update_error_budget(self, remaining_ratio: float) -> None:
        """Update error budget remaining.

        Args:
            remaining_ratio: Ratio of error budget remaining (0.0 - 1.0).
        """
        self.error_budget_remaining.set(max(0.0, min(1.0, remaining_ratio)))

    def update_utilization(self, mcp_server: str, ratio: float) -> None:
        """Update mcp_server utilization.

        Args:
            mcp_server: McpServer ID.
            ratio: Utilization ratio (0.0 - 1.0).
        """
        self.mcp_server_utilization.set(ratio, mcp_server=mcp_server)


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
