"""Background workers initialization."""

from typing import Any

from ...gc import BackgroundWorker, MetricsSnapshotWorker
from ...logging_config import get_logger
from ..state import PROVIDERS

logger = get_logger(__name__)

GC_WORKER_INTERVAL_SECONDS = 30
"""Interval for garbage collection worker."""

HEALTH_CHECK_INTERVAL_SECONDS = 60
"""Interval for health check worker."""

METRICS_SNAPSHOT_INTERVAL_SECONDS = 60
"""Interval for metrics history snapshot worker."""


def create_connection_log_worker(
    providers: Any,
    profiler: Any,
    config: dict | None = None,
) -> Any | None:
    """Create a ConnectionLogWorker if connection logging is enabled.

    Reads behavioral.connection_logging config keys and conditionally
    imports the enterprise ConnectionLogWorker. Returns None when disabled
    or when the enterprise module is not available.

    Args:
        providers: Dict-like mapping of provider_id -> provider.
        profiler: IBehavioralProfiler instance for recording observations.
        config: Full application configuration dict.

    Returns:
        ConnectionLogWorker instance (not started) or None.
    """
    config = config or {}
    cl_config = config.get("behavioral", {}).get("connection_logging", {})

    if not cl_config.get("enabled", False):
        return None

    try:
        from enterprise.behavioral.connection_log_worker import ConnectionLogWorker
    except ImportError:
        logger.warning(
            "connection_log_worker_unavailable",
            reason="enterprise_module_not_available",
        )
        return None

    interval_s = cl_config.get("interval_s", 30)
    docker_enabled = cl_config.get("docker_enabled", True)
    k8s_enabled = cl_config.get("k8s_enabled", True)
    k8s_namespace = cl_config.get("k8s_namespace", "default")

    worker = ConnectionLogWorker(
        providers=providers,
        profiler=profiler,
        interval_s=interval_s,
        docker_enabled=docker_enabled,
        k8s_enabled=k8s_enabled,
        k8s_namespace=k8s_namespace,
    )

    logger.info(
        "connection_log_worker_created",
        interval_s=interval_s,
        docker_enabled=docker_enabled,
        k8s_enabled=k8s_enabled,
        k8s_namespace=k8s_namespace,
    )
    return worker


def create_resource_monitor_worker(
    providers: Any,
    profiler: Any,
    resource_store: Any,
    event_bus: Any = None,
    metrics_publisher: Any = None,
    config: dict | None = None,
) -> Any | None:
    """Create a ResourceMonitorWorker if resource monitoring is enabled.

    Reads ``behavioral.resource_monitoring`` config keys and conditionally
    imports the enterprise ResourceMonitorWorker. Returns None when disabled
    or when the enterprise module is not available.

    Args:
        providers: Dict-like mapping of provider_id -> provider.
        profiler: IBehavioralProfiler instance for checking provider modes.
        resource_store: IResourceStore instance for persisting samples.
        event_bus: Optional event bus for publishing deviation events.
        metrics_publisher: Optional ObservabilityMetrics for Prometheus gauges.
        config: Full application configuration dict.

    Returns:
        ResourceMonitorWorker instance (not started) or None.
    """
    config = config or {}
    rm_config = config.get("behavioral", {}).get("resource_monitoring", {})

    if not rm_config.get("enabled", False):
        return None

    try:
        from enterprise.behavioral.resource_monitor import ResourceMonitorWorker
    except ImportError:
        logger.warning(
            "resource_monitor_worker_unavailable",
            reason="enterprise_module_not_available",
        )
        return None

    interval_s = rm_config.get("interval_s", 60)
    docker_enabled = rm_config.get("docker_enabled", True)
    k8s_enabled = rm_config.get("k8s_enabled", True)
    k8s_namespace = rm_config.get("k8s_namespace", "default")
    cpu_deviation_multiplier = rm_config.get("cpu_deviation_multiplier", 2.0)
    memory_deviation_multiplier = rm_config.get("memory_deviation_multiplier", 2.0)
    network_io_deviation_multiplier = rm_config.get("network_io_deviation_multiplier", 2.0)
    retention_days = rm_config.get("retention_days", 7)

    worker = ResourceMonitorWorker(
        providers=providers,
        resource_store=resource_store,
        profiler=profiler,
        event_bus=event_bus,
        metrics_publisher=metrics_publisher,
        interval_s=interval_s,
        docker_enabled=docker_enabled,
        k8s_enabled=k8s_enabled,
        k8s_namespace=k8s_namespace,
        cpu_deviation_multiplier=cpu_deviation_multiplier,
        memory_deviation_multiplier=memory_deviation_multiplier,
        network_io_deviation_multiplier=network_io_deviation_multiplier,
        retention_days=retention_days,
    )

    logger.info(
        "resource_monitor_worker_created",
        interval_s=interval_s,
        docker_enabled=docker_enabled,
        k8s_enabled=k8s_enabled,
        k8s_namespace=k8s_namespace,
        cpu_multiplier=cpu_deviation_multiplier,
        memory_multiplier=memory_deviation_multiplier,
        network_io_multiplier=network_io_deviation_multiplier,
    )
    return worker


def create_background_workers(
    profiler: Any = None,
    config: dict | None = None,
    resource_store: Any = None,
    event_bus: Any = None,
    metrics_publisher: Any = None,
) -> list[BackgroundWorker | MetricsSnapshotWorker]:
    """Create (but don't start) background workers.

    Args:
        profiler: Optional IBehavioralProfiler for connection log and resource workers.
        config: Optional full application config dict.
        resource_store: Optional IResourceStore for resource monitor worker.
        event_bus: Optional event bus for resource deviation events.
        metrics_publisher: Optional ObservabilityMetrics for Prometheus gauges.

    Returns:
        List of worker instances (not started).
    """
    gc_worker = BackgroundWorker(
        PROVIDERS,
        interval_s=GC_WORKER_INTERVAL_SECONDS,
        task="gc",
    )

    health_worker = BackgroundWorker(
        PROVIDERS,
        interval_s=HEALTH_CHECK_INTERVAL_SECONDS,
        task="health_check",
    )

    metrics_worker = MetricsSnapshotWorker(
        interval_s=METRICS_SNAPSHOT_INTERVAL_SECONDS,
    )

    workers: list[Any] = [gc_worker, health_worker, metrics_worker]
    worker_names = ["gc", "health_check", "metrics_snapshot"]

    # Conditionally add connection log worker
    if profiler is not None:
        cl_worker = create_connection_log_worker(
            providers=PROVIDERS,
            profiler=profiler,
            config=config,
        )
        if cl_worker is not None:
            workers.append(cl_worker)
            worker_names.append("connection_log")

    # Conditionally add resource monitor worker
    if profiler is not None and resource_store is not None:
        rm_worker = create_resource_monitor_worker(
            providers=PROVIDERS,
            profiler=profiler,
            resource_store=resource_store,
            event_bus=event_bus,
            metrics_publisher=metrics_publisher,
            config=config,
        )
        if rm_worker is not None:
            workers.append(rm_worker)
            worker_names.append("resource_monitor")

    logger.info("background_workers_created", workers=worker_names)
    return workers
