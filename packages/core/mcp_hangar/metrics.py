"""Prometheus metrics for MCP Hangar.

Production-grade metrics following Prometheus/OpenMetrics best practices:
- Consistent naming: mcp_hangar_<subsystem>_<metric>_<unit>
- Proper label cardinality control
- Thread-safe implementations
- Standard histogram buckets for different use cases
"""

from collections import defaultdict
from dataclasses import dataclass, field
from functools import wraps
import platform
import threading
import time

# =============================================================================
# Core Metric Types
# =============================================================================


@dataclass
class MetricSample:
    """Single metric sample with labels."""

    value: float
    labels: dict[str, str] = field(default_factory=dict)


class Counter:
    """
    Prometheus counter - monotonically increasing value.

    Use for: requests, errors, completions, bytes transferred.
    """

    def __init__(self, name: str, description: str, labels: list[str] = None):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self._values: dict[tuple, float] = defaultdict(float)
        self._created: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def inc(self, value: float = 1.0, **labels) -> None:
        """Increment counter by value (must be >= 0)."""
        if value < 0:
            raise ValueError("Counter can only increase")
        key = self._make_key(labels)
        with self._lock:
            if key not in self._created:
                self._created[key] = time.time()
            self._values[key] += value

    def _make_key(self, labels: dict) -> tuple:
        return tuple(labels.get(label_name, "") for label_name in self.label_names)

    def labels(self, **label_values) -> "_LabeledCounter":
        """Return counter with preset labels for reuse."""
        return _LabeledCounter(self, label_values)

    def collect(self) -> list[MetricSample]:
        """Collect all samples."""
        with self._lock:
            return [
                MetricSample(value=v, labels=dict(zip(self.label_names, k, strict=False)))
                for k, v in self._values.items()
            ]


class Gauge:
    """
    Prometheus gauge - value that can go up and down.

    Use for: in-progress operations, current state, temperature, queue size.
    """

    def __init__(self, name: str, description: str, labels: list[str] = None):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self._values: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, **labels) -> None:
        """Set gauge to value."""
        key = self._make_key(labels)
        with self._lock:
            self._values[key] = value

    def inc(self, value: float = 1.0, **labels) -> None:
        """Increment gauge."""
        key = self._make_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + value

    def dec(self, value: float = 1.0, **labels) -> None:
        """Decrement gauge."""
        key = self._make_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) - value

    def set_to_current_time(self, **labels) -> None:
        """Set gauge to current Unix timestamp."""
        self.set(time.time(), **labels)

    def _make_key(self, labels: dict) -> tuple:
        return tuple(labels.get(label_name, "") for label_name in self.label_names)

    def labels(self, **label_values) -> "_LabeledGauge":
        """Return gauge with preset labels."""
        return _LabeledGauge(self, label_values)

    def collect(self) -> list[MetricSample]:
        """Collect all samples."""
        with self._lock:
            return [
                MetricSample(value=v, labels=dict(zip(self.label_names, k, strict=False)))
                for k, v in self._values.items()
            ]


class Histogram:
    """
    Prometheus histogram - distribution of values in buckets.

    Use for: request latencies, response sizes.
    """

    # Standard bucket presets
    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
    LATENCY_BUCKETS = (
        0.001,
        0.0025,
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
        30.0,
    )
    SIZE_BUCKETS = (100, 1000, 10000, 100000, 1000000, 10000000)

    def __init__(
        self,
        name: str,
        description: str,
        labels: list[str] = None,
        buckets: tuple = None,
    ):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self.buckets = tuple(sorted(buckets or self.DEFAULT_BUCKETS)) + (float("inf"),)
        self._lock = threading.Lock()
        self._buckets: dict[tuple, dict[float, int]] = defaultdict(lambda: dict.fromkeys(self.buckets, 0))
        self._sums: dict[tuple, float] = defaultdict(float)
        self._counts: dict[tuple, int] = defaultdict(int)

    def observe(self, value: float, **labels) -> None:
        """Record an observation."""
        key = self._make_key(labels)
        with self._lock:
            self._sums[key] += value
            self._counts[key] += 1
            # Add to the first bucket that fits (buckets are sorted)
            for bucket in self.buckets:
                if value <= bucket:
                    self._buckets[key][bucket] += 1
                    break  # Only add to the first matching bucket

    def _make_key(self, labels: dict) -> tuple:
        return tuple(labels.get(label_name, "") for label_name in self.label_names)

    def labels(self, **label_values) -> "_LabeledHistogram":
        """Return histogram with preset labels."""
        return _LabeledHistogram(self, label_values)

    def time(self) -> "_Timer":
        """Context manager for timing code blocks."""
        return _Timer(self, {})

    def collect(self) -> tuple:
        """Collect buckets, sum, and count samples."""
        buckets = []
        sums = []
        counts = []

        with self._lock:
            for key, bucket_values in self._buckets.items():
                base_labels = dict(zip(self.label_names, key, strict=False))
                cumulative = 0
                for bucket in self.buckets:
                    cumulative += bucket_values.get(bucket, 0)
                    le = "+Inf" if bucket == float("inf") else str(bucket)
                    buckets.append(MetricSample(value=cumulative, labels={**base_labels, "le": le}))
                sums.append(MetricSample(value=self._sums[key], labels=base_labels))
                counts.append(MetricSample(value=self._counts[key], labels=base_labels))

        return buckets, sums, counts


class Summary:
    """
    Prometheus summary - streaming quantiles.

    Simpler implementation using min/max/avg for now.
    Use for: streaming data where quantiles aren't critical.
    """

    def __init__(self, name: str, description: str, labels: list[str] = None):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self._lock = threading.Lock()
        self._sums: dict[tuple, float] = defaultdict(float)
        self._counts: dict[tuple, int] = defaultdict(int)

    def observe(self, value: float, **labels) -> None:
        """Record an observation."""
        key = self._make_key(labels)
        with self._lock:
            self._sums[key] += value
            self._counts[key] += 1

    def _make_key(self, labels: dict) -> tuple:
        return tuple(labels.get(label_name, "") for label_name in self.label_names)

    def collect(self) -> tuple:
        """Collect sum and count samples."""
        sums = []
        counts = []
        with self._lock:
            for key in self._sums:
                base_labels = dict(zip(self.label_names, key, strict=False))
                sums.append(MetricSample(value=self._sums[key], labels=base_labels))
                counts.append(MetricSample(value=self._counts[key], labels=base_labels))
        return sums, counts


class Info:
    """
    Prometheus info metric - static key-value pairs.

    Use for: version info, build metadata, configuration.
    """

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self._labels: dict[str, str] = {}
        self._lock = threading.Lock()

    def info(self, **labels) -> None:
        """Set info labels."""
        with self._lock:
            self._labels = {k: str(v) for k, v in labels.items()}

    def collect(self) -> list[MetricSample]:
        """Collect info sample."""
        with self._lock:
            if self._labels:
                return [MetricSample(value=1.0, labels=self._labels)]
            return []


# =============================================================================
# Labeled Metric Helpers
# =============================================================================


class _LabeledCounter:
    """Counter with preset labels."""

    def __init__(self, counter: Counter, labels: dict):
        self._counter = counter
        self._labels = labels

    def inc(self, value: float = 1.0) -> None:
        self._counter.inc(value, **self._labels)


class _LabeledGauge:
    """Gauge with preset labels."""

    def __init__(self, gauge: Gauge, labels: dict):
        self._gauge = gauge
        self._labels = labels

    def set(self, value: float) -> None:
        self._gauge.set(value, **self._labels)

    def inc(self, value: float = 1.0) -> None:
        self._gauge.inc(value, **self._labels)

    def dec(self, value: float = 1.0) -> None:
        self._gauge.dec(value, **self._labels)


class _LabeledHistogram:
    """Histogram with preset labels."""

    def __init__(self, histogram: Histogram, labels: dict):
        self._histogram = histogram
        self._labels = labels

    def observe(self, value: float) -> None:
        self._histogram.observe(value, **self._labels)

    def time(self) -> "_Timer":
        return _Timer(self._histogram, self._labels)


class _Timer:
    """Context manager for timing operations."""

    def __init__(self, histogram: Histogram, labels: dict):
        self._histogram = histogram
        self._labels = labels
        self._start: float | None = None

    def __enter__(self) -> "_Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args) -> None:
        duration = time.perf_counter() - self._start
        self._histogram.observe(duration, **self._labels)


# =============================================================================
# Metrics Registry
# =============================================================================


class CollectorRegistry:
    """Central registry for all metrics with Prometheus exposition format output."""

    def __init__(self):
        self._collectors: dict[str, any] = {}
        self._lock = threading.Lock()

    def register(self, collector) -> None:
        """Register a metric collector."""
        with self._lock:
            if collector.name in self._collectors:
                raise ValueError(f"Metric {collector.name} already registered")
            self._collectors[collector.name] = collector

    def unregister(self, name: str) -> None:
        """Unregister a metric."""
        with self._lock:
            self._collectors.pop(name, None)

    def get(self, name: str):
        """Get collector by name."""
        return self._collectors.get(name)

    def collect(self) -> str:
        """Generate Prometheus exposition format output."""
        lines = []

        with self._lock:
            collectors = list(self._collectors.items())

        for name, collector in collectors:
            lines.extend(self._format_metric(name, collector))
            lines.append("")

        return "\n".join(lines)

    def _format_metric(self, name: str, collector) -> list[str]:
        """Format a single metric in Prometheus format."""
        lines = []
        lines.append(f"# HELP {name} {collector.description}")

        if isinstance(collector, Counter):
            lines.append(f"# TYPE {name} counter")
            for sample in collector.collect():
                labels = self._format_labels(sample.labels)
                lines.append(f"{name}_total{labels} {sample.value}")

        elif isinstance(collector, Gauge):
            lines.append(f"# TYPE {name} gauge")
            for sample in collector.collect():
                labels = self._format_labels(sample.labels)
                lines.append(f"{name}{labels} {sample.value}")

        elif isinstance(collector, Histogram):
            lines.append(f"# TYPE {name} histogram")
            buckets, sums, counts = collector.collect()
            for sample in buckets:
                labels = self._format_labels(sample.labels)
                lines.append(f"{name}_bucket{labels} {int(sample.value)}")
            for sample in sums:
                labels = self._format_labels(sample.labels)
                lines.append(f"{name}_sum{labels} {sample.value}")
            for sample in counts:
                labels = self._format_labels(sample.labels)
                lines.append(f"{name}_count{labels} {int(sample.value)}")

        elif isinstance(collector, Summary):
            lines.append(f"# TYPE {name} summary")
            sums, counts = collector.collect()
            for sample in sums:
                labels = self._format_labels(sample.labels)
                lines.append(f"{name}_sum{labels} {sample.value}")
            for sample in counts:
                labels = self._format_labels(sample.labels)
                lines.append(f"{name}_count{labels} {int(sample.value)}")

        elif isinstance(collector, Info):
            lines.append(f"# TYPE {name}_info gauge")
            for sample in collector.collect():
                labels = self._format_labels(sample.labels)
                lines.append(f"{name}_info{labels} 1")

        return lines

    def _format_labels(self, labels: dict[str, str]) -> str:
        """Format labels in Prometheus format."""
        if not labels:
            return ""
        # Escape label values properly
        escaped = []
        for k, v in sorted(labels.items()):
            if v is None:
                v = ""
            v = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            escaped.append(f'{k}="{v}"')
        return "{" + ",".join(escaped) + "}"


# =============================================================================
# Global Registry
# =============================================================================

REGISTRY = CollectorRegistry()


# =============================================================================
# MCP Hangar Metrics - Following Best Practices
# =============================================================================

# -----------------------------------------------------------------------------
# Build/Version Info
# -----------------------------------------------------------------------------

BUILD_INFO = Info(
    name="mcp_hangar_build",
    description="Build and version information for MCP Hangar",
)

# -----------------------------------------------------------------------------
# Process Metrics
# -----------------------------------------------------------------------------

PROCESS_START_TIME = Gauge(
    name="mcp_hangar_process_start_time_seconds",
    description="Unix timestamp of process start time",
)

# -----------------------------------------------------------------------------
# Provider Lifecycle Metrics
# -----------------------------------------------------------------------------

PROVIDER_INFO = Gauge(
    name="mcp_hangar_provider_info",
    description="Provider configuration info (always 1, labels contain metadata)",
    labels=["provider", "mode"],
)

PROVIDER_STATE_CURRENT = Gauge(
    name="mcp_hangar_provider_state",
    description="Current provider state (0=cold, 1=initializing, 2=ready, 3=degraded, 4=dead)",
    labels=["provider"],
)

PROVIDER_UP = Gauge(
    name="mcp_hangar_provider_up",
    description="Whether provider is up and ready (1=up, 0=down)",
    labels=["provider"],
)

PROVIDER_INITIALIZED = Gauge(
    name="mcp_hangar_provider_initialized",
    description="Whether provider has been initialized at least once (1=yes, 0=no/cold)",
    labels=["provider"],
)

PROVIDER_LAST_STATE_CHANGE_SECONDS = Gauge(
    name="mcp_hangar_provider_last_state_change_timestamp_seconds",
    description="Unix timestamp of last provider state change",
    labels=["provider"],
)

PROVIDER_STARTS_TOTAL = Counter(
    name="mcp_hangar_provider_starts",
    description="Total number of provider start attempts",
    labels=["provider", "result"],  # result: success, failure
)

PROVIDER_STOPS_TOTAL = Counter(
    name="mcp_hangar_provider_stops",
    description="Total number of provider stops",
    labels=["provider", "reason"],  # reason: idle, manual, error, gc
)

PROVIDER_COLD_START_SECONDS = Histogram(
    name="mcp_hangar_provider_cold_start_seconds",
    description="Time from cold start to ready state (critical UX metric)",
    labels=["provider", "mode"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 30.0, 60.0),
)

PROVIDER_COLD_START_IN_PROGRESS = Gauge(
    name="mcp_hangar_provider_cold_start_in_progress",
    description="Number of providers currently in cold start",
    labels=["provider"],
)

# -----------------------------------------------------------------------------
# Tool Invocation Metrics (RED method: Rate, Errors, Duration)
# -----------------------------------------------------------------------------

TOOL_CALLS_TOTAL = Counter(
    name="mcp_hangar_tool_calls",
    description="Total number of tool calls",
    labels=["provider", "tool", "status"],  # status: success, error
)

TOOL_CALL_DURATION_SECONDS = Histogram(
    name="mcp_hangar_tool_call_duration_seconds",
    description="Duration of tool calls in seconds",
    labels=["provider", "tool"],
    buckets=Histogram.LATENCY_BUCKETS,
)

TOOL_CALL_ERRORS_TOTAL = Counter(
    name="mcp_hangar_tool_call_errors",
    description="Total number of tool call errors by error type",
    labels=["provider", "tool", "error_type"],
)

# -----------------------------------------------------------------------------
# Health Check Metrics
# -----------------------------------------------------------------------------

HEALTH_CHECK_TOTAL = Counter(
    name="mcp_hangar_health_checks",
    description="Total number of health check executions",
    labels=["provider", "result"],  # result: cold, healthy, unhealthy
)

HEALTH_CHECK_DURATION_SECONDS = Histogram(
    name="mcp_hangar_health_check_duration_seconds",
    description="Duration of health checks in seconds",
    labels=["provider"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

HEALTH_CHECK_CONSECUTIVE_FAILURES = Gauge(
    name="mcp_hangar_health_check_consecutive_failures",
    description="Number of consecutive health check failures",
    labels=["provider"],
)

# -----------------------------------------------------------------------------
# Connection Pool Metrics
# -----------------------------------------------------------------------------

CONNECTIONS_ACTIVE = Gauge(
    name="mcp_hangar_connections_active",
    description="Number of active connections to providers",
    labels=["provider"],
)

CONNECTIONS_TOTAL = Counter(
    name="mcp_hangar_connections",
    description="Total number of connections established",
    labels=["provider", "result"],
)

CONNECTION_DURATION_SECONDS = Histogram(
    name="mcp_hangar_connection_duration_seconds",
    description="Duration of provider connections in seconds",
    labels=["provider"],
    buckets=(1, 5, 10, 30, 60, 300, 600, 1800, 3600),
)

# -----------------------------------------------------------------------------
# Message Metrics
# -----------------------------------------------------------------------------

MESSAGES_SENT_TOTAL = Counter(
    name="mcp_hangar_messages_sent",
    description="Total number of JSON-RPC messages sent",
    labels=["provider", "method"],
)

MESSAGES_RECEIVED_TOTAL = Counter(
    name="mcp_hangar_messages_received",
    description="Total number of JSON-RPC messages received",
    labels=["provider", "type"],  # values: response, notification, error
)

MESSAGE_SIZE_BYTES = Histogram(
    name="mcp_hangar_message_size_bytes",
    description="Size of JSON-RPC messages in bytes",
    labels=["provider", "direction"],  # direction: sent, received
    buckets=Histogram.SIZE_BUCKETS,
)

# -----------------------------------------------------------------------------
# GC (Garbage Collection) Metrics
# -----------------------------------------------------------------------------

GC_CYCLES_TOTAL = Counter(
    name="mcp_hangar_gc_cycles",
    description="Total number of garbage collection cycles",
)

GC_CYCLE_DURATION_SECONDS = Histogram(
    name="mcp_hangar_gc_cycle_duration_seconds",
    description="Duration of garbage collection cycles in seconds",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
)

GC_PROVIDERS_COLLECTED_TOTAL = Counter(
    name="mcp_hangar_gc_providers_collected",
    description="Total number of providers collected by GC",
    labels=["reason"],  # reason: idle, dead, error
)

# -----------------------------------------------------------------------------
# Error Metrics
# -----------------------------------------------------------------------------

ERRORS_TOTAL = Counter(
    name="mcp_hangar_errors",
    description="Total number of errors by type and component",
    labels=["component", "error_type"],  # component: provider, tool, health, gc, server
)

# -----------------------------------------------------------------------------
# Rate Limiter Metrics
# -----------------------------------------------------------------------------

RATE_LIMIT_HITS_TOTAL = Counter(
    name="mcp_hangar_rate_limit_hits",
    description="Total number of requests that hit rate limits",
    labels=["endpoint"],
)

# -----------------------------------------------------------------------------
# Discovery Metrics
# -----------------------------------------------------------------------------

DISCOVERY_SOURCES_TOTAL = Gauge(
    name="mcp_hangar_discovery_sources",
    description="Number of configured discovery sources",
    labels=["source_type", "mode"],
)

DISCOVERY_SOURCES_HEALTHY = Gauge(
    name="mcp_hangar_discovery_sources_healthy",
    description="Whether discovery source is healthy (1=healthy, 0=unhealthy)",
    labels=["source_type"],
)

DISCOVERY_PROVIDERS_TOTAL = Gauge(
    name="mcp_hangar_discovery_providers",
    description="Number of discovered providers",
    labels=["source_type", "status"],  # status: discovered, registered, quarantined
)

DISCOVERY_CYCLES_TOTAL = Counter(
    name="mcp_hangar_discovery_cycles",
    description="Total number of discovery cycles executed",
    labels=["source_type"],
)

DISCOVERY_CYCLE_DURATION_SECONDS = Histogram(
    name="mcp_hangar_discovery_cycle_duration_seconds",
    description="Duration of discovery cycles in seconds",
    labels=["source_type"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

DISCOVERY_REGISTRATIONS_TOTAL = Counter(
    name="mcp_hangar_discovery_registrations",
    description="Total provider registrations from discovery",
    labels=["source_type"],
)

DISCOVERY_DEREGISTRATIONS_TOTAL = Counter(
    name="mcp_hangar_discovery_deregistrations",
    description="Total provider deregistrations from discovery",
    labels=["source_type", "reason"],  # reason: ttl_expired, source_removed, manual
)

DISCOVERY_CONFLICTS_TOTAL = Counter(
    name="mcp_hangar_discovery_conflicts",
    description="Total discovery conflicts",
    labels=["conflict_type"],  # conflict_type: static_wins, source_priority
)

DISCOVERY_QUARANTINE_TOTAL = Counter(
    name="mcp_hangar_discovery_quarantine",
    description="Total providers quarantined",
    labels=["reason"],  # reason: health_check_failed, validation_failed, rate_limited
)

DISCOVERY_ERRORS_TOTAL = Counter(
    name="mcp_hangar_discovery_errors",
    description="Total discovery errors",
    labels=["source_type", "error_type"],
)

DISCOVERY_LAST_CYCLE_TIMESTAMP = Gauge(
    name="mcp_hangar_discovery_last_cycle_timestamp_seconds",
    description="Unix timestamp of last discovery cycle",
    labels=["source_type"],
)

# -----------------------------------------------------------------------------
# HTTP Transport Metrics (for remote providers)
# -----------------------------------------------------------------------------

HTTP_REQUESTS_TOTAL = Counter(
    name="mcp_hangar_http_requests",
    description="Total number of HTTP requests to remote providers",
    labels=["provider", "method", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    name="mcp_hangar_http_request_duration_seconds",
    description="Duration of HTTP requests to remote providers in seconds",
    labels=["provider", "method"],
    buckets=Histogram.LATENCY_BUCKETS,
)

HTTP_ERRORS_TOTAL = Counter(
    name="mcp_hangar_http_errors",
    description="Total number of HTTP errors by type",
    labels=["provider", "error_type"],  # error_type: connection_refused, timeout, auth_failed, ssl_error
)

HTTP_RETRIES_TOTAL = Counter(
    name="mcp_hangar_http_retries",
    description="Total number of HTTP request retries",
    labels=["provider", "retry_reason"],  # retry_reason: 502, 503, 504, connection_error
)

HTTP_CONNECTION_POOL_SIZE = Gauge(
    name="mcp_hangar_http_connection_pool_size",
    description="Current number of connections in HTTP connection pool",
    labels=["provider"],
)

HTTP_SSE_STREAMS_ACTIVE = Gauge(
    name="mcp_hangar_http_sse_streams_active",
    description="Number of active SSE streams to remote providers",
    labels=["provider"],
)

HTTP_SSE_EVENTS_TOTAL = Counter(
    name="mcp_hangar_http_sse_events",
    description="Total number of SSE events received from remote providers",
    labels=["provider", "event_type"],  # event_type: message, notification, error
)

# -----------------------------------------------------------------------------
# Batch Invocation Metrics
# -----------------------------------------------------------------------------

BATCH_CALLS_TOTAL = Counter(
    name="mcp_hangar_batch_calls",
    description="Total number of batch invocations",
    labels=["result"],  # result: success, partial, failure, validation_error
)

BATCH_VALIDATION_FAILURES_TOTAL = Counter(
    name="mcp_hangar_batch_validation_failures",
    description="Total number of batch validation failures",
)

BATCH_SIZE_HISTOGRAM = Histogram(
    name="mcp_hangar_batch_size",
    description="Distribution of batch sizes (number of calls per batch)",
    buckets=(1, 2, 5, 10, 20, 50, 100),
)

BATCH_DURATION_SECONDS = Histogram(
    name="mcp_hangar_batch_duration_seconds",
    description="Duration of batch invocations in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

BATCH_CONCURRENCY_GAUGE = Gauge(
    name="mcp_hangar_batch_concurrency",
    description="Current number of parallel batch executions",
)

BATCH_TRUNCATIONS_TOTAL = Counter(
    name="mcp_hangar_batch_truncations",
    description="Total number of response truncations in batches",
    labels=["reason"],  # reason: per_call, total_size
)

BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL = Counter(
    name="mcp_hangar_batch_circuit_breaker_rejections",
    description="Total calls rejected due to circuit breaker in batches",
    labels=["provider"],
)

BATCH_CANCELLATIONS_TOTAL = Counter(
    name="mcp_hangar_batch_cancellations",
    description="Total number of batch cancellations",
    labels=["reason"],  # reason: timeout, fail_fast
)


# =============================================================================
# Register All Metrics
# =============================================================================


def _register_all_metrics():
    """Register all predefined metrics."""
    metrics = [
        BUILD_INFO,
        PROCESS_START_TIME,
        PROVIDER_INFO,
        PROVIDER_STATE_CURRENT,
        PROVIDER_UP,
        PROVIDER_INITIALIZED,
        PROVIDER_LAST_STATE_CHANGE_SECONDS,
        PROVIDER_STARTS_TOTAL,
        PROVIDER_STOPS_TOTAL,
        PROVIDER_COLD_START_SECONDS,
        PROVIDER_COLD_START_IN_PROGRESS,
        TOOL_CALLS_TOTAL,
        TOOL_CALL_DURATION_SECONDS,
        TOOL_CALL_ERRORS_TOTAL,
        HEALTH_CHECK_TOTAL,
        HEALTH_CHECK_DURATION_SECONDS,
        HEALTH_CHECK_CONSECUTIVE_FAILURES,
        CONNECTIONS_ACTIVE,
        CONNECTIONS_TOTAL,
        CONNECTION_DURATION_SECONDS,
        MESSAGES_SENT_TOTAL,
        MESSAGES_RECEIVED_TOTAL,
        MESSAGE_SIZE_BYTES,
        GC_CYCLES_TOTAL,
        GC_CYCLE_DURATION_SECONDS,
        GC_PROVIDERS_COLLECTED_TOTAL,
        ERRORS_TOTAL,
        RATE_LIMIT_HITS_TOTAL,
        # Discovery metrics
        DISCOVERY_SOURCES_TOTAL,
        DISCOVERY_SOURCES_HEALTHY,
        DISCOVERY_PROVIDERS_TOTAL,
        DISCOVERY_CYCLES_TOTAL,
        DISCOVERY_CYCLE_DURATION_SECONDS,
        DISCOVERY_REGISTRATIONS_TOTAL,
        DISCOVERY_DEREGISTRATIONS_TOTAL,
        DISCOVERY_CONFLICTS_TOTAL,
        DISCOVERY_QUARANTINE_TOTAL,
        DISCOVERY_ERRORS_TOTAL,
        DISCOVERY_LAST_CYCLE_TIMESTAMP,
        # HTTP transport metrics
        HTTP_REQUESTS_TOTAL,
        HTTP_REQUEST_DURATION_SECONDS,
        HTTP_ERRORS_TOTAL,
        HTTP_RETRIES_TOTAL,
        HTTP_CONNECTION_POOL_SIZE,
        HTTP_SSE_STREAMS_ACTIVE,
        HTTP_SSE_EVENTS_TOTAL,
        # Batch invocation metrics
        BATCH_CALLS_TOTAL,
        BATCH_VALIDATION_FAILURES_TOTAL,
        BATCH_SIZE_HISTOGRAM,
        BATCH_DURATION_SECONDS,
        BATCH_CONCURRENCY_GAUGE,
        BATCH_TRUNCATIONS_TOTAL,
        BATCH_CIRCUIT_BREAKER_REJECTIONS_TOTAL,
        BATCH_CANCELLATIONS_TOTAL,
    ]
    for metric in metrics:
        REGISTRY.register(metric)


_register_all_metrics()


# =============================================================================
# Convenience Functions
# =============================================================================


def get_metrics() -> str:
    """Get all metrics in Prometheus exposition format."""
    return REGISTRY.collect()


def init_metrics(version: str = "1.0.0"):
    """Initialize metrics on server startup."""
    BUILD_INFO.info(
        version=version,
        python_version=platform.python_version(),
        platform=platform.system(),
    )
    PROCESS_START_TIME.set(time.time())


def observe_tool_call(provider: str, tool: str, duration: float, success: bool, error_type: str = None):
    """Record a tool call observation."""
    status = "success" if success else "error"
    TOOL_CALLS_TOTAL.inc(provider=provider, tool=tool, status=status)
    TOOL_CALL_DURATION_SECONDS.observe(duration, provider=provider, tool=tool)
    if not success and error_type:
        TOOL_CALL_ERRORS_TOTAL.inc(provider=provider, tool=tool, error_type=error_type)


def observe_health_check(
    provider: str,
    duration: float,
    healthy: bool,
    is_cold: bool = False,
    consecutive_failures: int = 0,
):
    """Record a health check observation.

    Args:
        provider: Provider ID
        duration: Health check duration in seconds
        healthy: Whether the check passed (only meaningful if not cold)
        is_cold: Whether provider is in cold state (not started yet)
        consecutive_failures: Number of consecutive failures
    """
    if is_cold:
        result = "cold"
    elif healthy:
        result = "healthy"
    else:
        result = "unhealthy"

    HEALTH_CHECK_TOTAL.inc(provider=provider, result=result)
    HEALTH_CHECK_DURATION_SECONDS.observe(duration, provider=provider)
    HEALTH_CHECK_CONSECUTIVE_FAILURES.set(consecutive_failures, provider=provider)


def update_provider_state(provider: str, state: str, mode: str = "subprocess"):
    """Update provider state metrics."""
    state_map = {"cold": 0, "initializing": 1, "ready": 2, "degraded": 3, "dead": 4}
    PROVIDER_STATE_CURRENT.set(state_map.get(state, 0), provider=provider)
    PROVIDER_UP.set(1 if state == "ready" else 0, provider=provider)
    PROVIDER_INITIALIZED.set(0 if state == "cold" else 1, provider=provider)
    PROVIDER_INFO.set(1, provider=provider, mode=mode)
    PROVIDER_LAST_STATE_CHANGE_SECONDS.set(time.time(), provider=provider)


def record_provider_start(provider: str, success: bool):
    """Record a provider start attempt."""
    result = "success" if success else "failure"
    PROVIDER_STARTS_TOTAL.inc(provider=provider, result=result)
    if success:
        PROVIDER_INITIALIZED.set(1, provider=provider)


def record_provider_stop(provider: str, reason: str):
    """Record a provider stop."""
    PROVIDER_STOPS_TOTAL.inc(provider=provider, reason=reason)


def record_cold_start(provider: str, duration: float, mode: str = "subprocess"):
    """Record cold start duration - the critical UX metric.

    This measures time from user request to provider ready state.
    High values here directly impact user experience.

    Args:
        provider: Provider ID
        duration: Time in seconds from start to ready
        mode: Provider mode (subprocess, docker, etc.)
    """
    PROVIDER_COLD_START_SECONDS.observe(duration, provider=provider, mode=mode)


def cold_start_begin(provider: str):
    """Mark beginning of cold start (for in-progress tracking)."""
    PROVIDER_COLD_START_IN_PROGRESS.set(1, provider=provider)


def cold_start_end(provider: str):
    """Mark end of cold start."""
    PROVIDER_COLD_START_IN_PROGRESS.set(0, provider=provider)


def record_gc_cycle(duration: float, collected: dict[str, int] = None):
    """Record a GC cycle."""
    GC_CYCLES_TOTAL.inc()
    GC_CYCLE_DURATION_SECONDS.observe(duration)
    if collected:
        for reason, count in collected.items():
            for _ in range(count):
                GC_PROVIDERS_COLLECTED_TOTAL.inc(reason=reason)


def record_error(component: str, error_type: str):
    """Record an error."""
    ERRORS_TOTAL.inc(component=component, error_type=error_type)


# =============================================================================
# Discovery Metrics Functions
# =============================================================================


def update_discovery_source(source_type: str, mode: str, is_healthy: bool, providers_count: int):
    """Update discovery source metrics.

    Args:
        source_type: Type of source (filesystem, docker, kubernetes, entrypoint)
        mode: Discovery mode (additive, authoritative)
        is_healthy: Whether the source is healthy
        providers_count: Number of providers discovered by this source
    """
    DISCOVERY_SOURCES_TOTAL.set(1, source_type=source_type, mode=mode)
    DISCOVERY_SOURCES_HEALTHY.set(1 if is_healthy else 0, source_type=source_type)
    DISCOVERY_PROVIDERS_TOTAL.set(providers_count, source_type=source_type, status="discovered")


def record_discovery_cycle(
    source_type: str,
    duration: float,
    discovered: int = 0,
    registered: int = 0,
    quarantined: int = 0,
):
    """Record a discovery cycle execution.

    Args:
        source_type: Type of source
        duration: Duration of the cycle in seconds
        discovered: Number of providers discovered
        registered: Number of providers registered
        quarantined: Number of providers quarantined
    """
    DISCOVERY_CYCLES_TOTAL.inc(source_type=source_type)
    DISCOVERY_CYCLE_DURATION_SECONDS.observe(duration, source_type=source_type)
    DISCOVERY_LAST_CYCLE_TIMESTAMP.set(time.time(), source_type=source_type)

    # Update provider counts
    DISCOVERY_PROVIDERS_TOTAL.set(discovered, source_type=source_type, status="discovered")
    DISCOVERY_PROVIDERS_TOTAL.set(registered, source_type=source_type, status="registered")
    DISCOVERY_PROVIDERS_TOTAL.set(quarantined, source_type=source_type, status="quarantined")


def record_discovery_registration(source_type: str):
    """Record a provider registration from discovery."""
    DISCOVERY_REGISTRATIONS_TOTAL.inc(source_type=source_type)


def record_discovery_deregistration(source_type: str, reason: str):
    """Record a provider deregistration from discovery.

    Args:
        source_type: Type of source
        reason: Reason for deregistration (ttl_expired, source_removed, manual)
    """
    DISCOVERY_DEREGISTRATIONS_TOTAL.inc(source_type=source_type, reason=reason)


def record_discovery_conflict(conflict_type: str):
    """Record a discovery conflict.

    Args:
        conflict_type: Type of conflict (static_wins, source_priority)
    """
    DISCOVERY_CONFLICTS_TOTAL.inc(conflict_type=conflict_type)


def record_discovery_quarantine(reason: str):
    """Record a provider quarantine.

    Args:
        reason: Reason for quarantine (health_check_failed, validation_failed, rate_limited)
    """
    DISCOVERY_QUARANTINE_TOTAL.inc(reason=reason)


def record_discovery_error(source_type: str, error_type: str):
    """Record a discovery error.

    Args:
        source_type: Type of source
        error_type: Type of error
    """
    DISCOVERY_ERRORS_TOTAL.inc(source_type=source_type, error_type=error_type)


# =============================================================================
# Timing Decorator
# =============================================================================


def timed(histogram: Histogram, **labels):
    """Decorator to time function execution."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with histogram.labels(**labels).time():
                return func(*args, **kwargs)

        return wrapper

    return decorator
