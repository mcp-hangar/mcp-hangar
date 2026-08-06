"""Background workers initialization."""

from typing import Any, cast

from ...gc import BackgroundWorker, MetricsSnapshotWorker
from ...logging_config import get_logger
from ..state import get_runtime
from .coordination import may_manage

logger = get_logger(__name__)

GC_WORKER_INTERVAL_SECONDS = 30
"""Interval for garbage collection worker."""

HEALTH_CHECK_INTERVAL_SECONDS = 60
"""Interval for health check worker."""

METRICS_SNAPSHOT_INTERVAL_SECONDS = 60
"""Interval for metrics history snapshot worker."""


def create_background_workers(
    config: dict[str, Any] | None = None,
) -> list[BackgroundWorker | MetricsSnapshotWorker]:
    """Create (but don't start) background workers.

    Args:
        config: Optional full application config dict.

    Returns:
        List of worker instances (not started).
    """
    mcp_servers = get_runtime().repository

    gc_worker = BackgroundWorker(
        cast(Any, mcp_servers),
        interval_s=GC_WORKER_INTERVAL_SECONDS,
        task="gc",
    )

    health_worker = BackgroundWorker(
        cast(Any, mcp_servers),
        interval_s=HEALTH_CHECK_INTERVAL_SECONDS,
        task="health_check",
    )

    metrics_worker = MetricsSnapshotWorker(
        interval_s=METRICS_SNAPSHOT_INTERVAL_SECONDS,
        # The only one of the three that writes to shared storage. GC and health
        # checks act on *this* replica's own processes and connections, so
        # gating them would leak idle subprocesses on every follower and leave
        # followers unable to tell that an upstream had died.
        may_manage=may_manage,
    )

    workers: list[Any] = [gc_worker, health_worker, metrics_worker]
    worker_names = ["gc", "health_check", "metrics_snapshot"]

    logger.info("background_workers_created", workers=worker_names)
    return workers
