"""Discovery Metrics.

Prometheus metrics for provider discovery observability.
Tracks discovery cycles, registrations, conflicts, and validation times.
"""

from functools import wraps
import time
from typing import Callable
import logging

logger = logging.getLogger(__name__)

# Optional prometheus dependency
try:
    from prometheus_client import Counter, Gauge, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.debug("prometheus_client not installed, metrics will be no-ops")


class DiscoveryMetrics:
    """Prometheus metrics for provider discovery.

    Metrics:
        - mcp_hangar_discovery_providers_total: Gauge of providers per source/status
        - mcp_hangar_discovery_registrations_total: Counter of registrations
        - mcp_hangar_discovery_deregistrations_total: Counter of deregistrations
        - mcp_hangar_discovery_errors_total: Counter of errors
        - mcp_hangar_discovery_conflicts_total: Counter of conflicts
        - mcp_hangar_discovery_quarantine_total: Counter of quarantined providers
        - mcp_hangar_discovery_latency_seconds: Histogram of discovery cycle duration
        - mcp_hangar_discovery_validation_duration_seconds: Histogram of validation time
    """

    def __init__(self, prefix: str = "mcp_hangar_discovery"):
        """Initialize discovery metrics.

        Args:
            prefix: Metric name prefix
        """
        self.prefix = prefix
        self._enabled = PROMETHEUS_AVAILABLE

        if not self._enabled:
            logger.warning("Prometheus metrics disabled (prometheus_client not installed)")
            return

        # Gauges
        self.providers_total = Gauge(
            f"{prefix}_providers_total", "Number of discovered providers", ["source", "status"]
        )

        # Counters
        self.registrations_total = Counter(
            f"{prefix}_registrations_total", "Total provider registrations from discovery", ["source"]
        )

        self.deregistrations_total = Counter(
            f"{prefix}_deregistrations_total", "Total provider deregistrations", ["source", "reason"]
        )

        self.errors_total = Counter(f"{prefix}_errors_total", "Total discovery errors", ["source", "error_type"])

        self.conflicts_total = Counter(f"{prefix}_conflicts_total", "Total discovery conflicts", ["type"])

        self.quarantine_total = Counter(f"{prefix}_quarantine_total", "Total quarantined providers", ["reason"])

        self.validation_failures_total = Counter(
            f"{prefix}_validation_failures_total", "Total validation failures", ["source", "validation_type"]
        )

        # Histograms
        self.latency_seconds = Histogram(
            f"{prefix}_latency_seconds",
            "Discovery cycle duration",
            ["source"],
            buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
        )

        self.validation_duration_seconds = Histogram(
            f"{prefix}_validation_duration_seconds",
            "Provider validation duration",
            ["source"],
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
        )

        self.cycle_duration_seconds = Histogram(
            f"{prefix}_cycle_duration_seconds",
            "Full discovery cycle duration",
            buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
        )

    def set_providers_count(self, source: str, status: str, count: int) -> None:
        """Set provider count for a source/status combination.

        Args:
            source: Discovery source type
            status: Provider status (discovered, registered, etc.)
            count: Number of providers
        """
        if self._enabled:
            self.providers_total.labels(source=source, status=status).set(count)

    def inc_registrations(self, source: str) -> None:
        """Increment registration counter.

        Args:
            source: Discovery source type
        """
        if self._enabled:
            self.registrations_total.labels(source=source).inc()

    def inc_deregistrations(self, source: str, reason: str) -> None:
        """Increment deregistration counter.

        Args:
            source: Discovery source type
            reason: Reason for deregistration
        """
        if self._enabled:
            self.deregistrations_total.labels(source=source, reason=reason).inc()

    def inc_errors(self, source: str, error_type: str) -> None:
        """Increment error counter.

        Args:
            source: Discovery source type
            error_type: Type of error
        """
        if self._enabled:
            self.errors_total.labels(source=source, error_type=error_type).inc()

    def inc_conflicts(self, conflict_type: str) -> None:
        """Increment conflict counter.

        Args:
            conflict_type: Type of conflict
        """
        if self._enabled:
            self.conflicts_total.labels(type=conflict_type).inc()

    def inc_quarantine(self, reason: str) -> None:
        """Increment quarantine counter.

        Args:
            reason: Reason for quarantine
        """
        if self._enabled:
            self.quarantine_total.labels(reason=reason).inc()

    def inc_validation_failures(self, source: str, validation_type: str) -> None:
        """Increment validation failure counter.

        Args:
            source: Discovery source type
            validation_type: Type of validation that failed
        """
        if self._enabled:
            self.validation_failures_total.labels(source=source, validation_type=validation_type).inc()

    def observe_latency(self, source: str, duration_seconds: float) -> None:
        """Record discovery latency.

        Args:
            source: Discovery source type
            duration_seconds: Duration in seconds
        """
        if self._enabled:
            self.latency_seconds.labels(source=source).observe(duration_seconds)

    def observe_validation_duration(self, source: str, duration_seconds: float) -> None:
        """Record validation duration.

        Args:
            source: Discovery source type
            duration_seconds: Duration in seconds
        """
        if self._enabled:
            self.validation_duration_seconds.labels(source=source).observe(duration_seconds)

    def observe_cycle_duration(self, duration_seconds: float) -> None:
        """Record full discovery cycle duration.

        Args:
            duration_seconds: Duration in seconds
        """
        if self._enabled:
            self.cycle_duration_seconds.observe(duration_seconds)


# Global metrics instance
_metrics: DiscoveryMetrics = None


def get_discovery_metrics() -> DiscoveryMetrics:
    """Get or create global discovery metrics instance.

    Returns:
        DiscoveryMetrics instance
    """
    global _metrics
    if _metrics is None:
        _metrics = DiscoveryMetrics()
    return _metrics


def observe_discovery(source_type: str):
    """Decorator to observe discovery cycle metrics.

    Args:
        source_type: Discovery source type for labeling

    Returns:
        Decorator function
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            metrics = get_discovery_metrics()
            start = time.perf_counter()

            try:
                result = await func(*args, **kwargs)

                # Update provider count if result is a list
                if isinstance(result, list):
                    metrics.set_providers_count(source=source_type, status="discovered", count=len(result))

                return result

            except Exception as e:
                metrics.inc_errors(source=source_type, error_type=type(e).__name__)
                raise

            finally:
                duration = time.perf_counter() - start
                metrics.observe_latency(source=source_type, duration_seconds=duration)

        return wrapper

    return decorator


def observe_validation(source_type: str):
    """Decorator to observe validation metrics.

    Args:
        source_type: Discovery source type for labeling

    Returns:
        Decorator function
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            metrics = get_discovery_metrics()
            start = time.perf_counter()

            try:
                result = await func(*args, **kwargs)
                return result

            finally:
                duration = time.perf_counter() - start
                metrics.observe_validation_duration(source=source_type, duration_seconds=duration)

        return wrapper

    return decorator
