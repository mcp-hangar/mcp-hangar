"""Background workers initialization."""

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


def create_background_workers() -> list[BackgroundWorker | MetricsSnapshotWorker]:
    """Create (but don't start) background workers.

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

    logger.info("background_workers_created", workers=["gc", "health_check", "metrics_snapshot"])
    return [gc_worker, health_worker, metrics_worker]
