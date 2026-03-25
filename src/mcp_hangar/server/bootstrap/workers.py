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


def create_background_workers(
    profiler: Any = None,
    config: dict | None = None,
) -> list[BackgroundWorker | MetricsSnapshotWorker]:
    """Create (but don't start) background workers.

    Args:
        profiler: Optional IBehavioralProfiler for connection log worker.
        config: Optional full application config dict.

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

    logger.info("background_workers_created", workers=worker_names)
    return workers
