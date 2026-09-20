"""Background workers: built by bootstrap, started and stopped here."""

import time
from collections.abc import Sequence
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

WORKER_STOP_TIMEOUT_SECONDS = 10.0
"""How long `stop_background_workers` waits, in total, for the workers' threads to end."""


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


def start_background_workers(workers: Sequence[Any]) -> None:
    """Start *workers*, the ones `bootstrap()` built into the application context.

    `ServerLifecycle.start` and the `Hangar` facade both start them here, so
    `serve` and an embedded gateway run the same set (#1435).

    Args:
        workers: The context's `background_workers`.
    """
    for worker in workers:
        worker.start()

    logger.info("background_workers_started", workers=[worker.task for worker in workers])


def stop_background_workers(workers: Sequence[Any], timeout_s: float = WORKER_STOP_TIMEOUT_SECONDS) -> None:
    """Stop *workers* and wait for their threads to end.

    Every worker is told to stop before any is waited on, so they wind down
    together. The wait is bounded by *timeout_s* in total: a cycle stuck on an
    upstream must not hold shutdown for ever, so a worker still running then
    is logged and left. A worker that fails to stop is logged, and the others
    are still stopped.

    Args:
        workers: The context's `background_workers`.
        timeout_s: The most this waits for all of them together.
    """
    for worker in workers:
        try:
            worker.stop()
        except Exception as e:  # noqa: BLE001 -- fault-barrier: shutdown must complete even if individual worker stop fails
            logger.warning("worker_stop_failed", task=worker.task, error=str(e))

    deadline = time.monotonic() + timeout_s
    for worker in workers:
        try:
            ended = worker.join(max(0.0, deadline - time.monotonic()))
        except Exception as e:  # noqa: BLE001 -- fault-barrier: one worker's failed wait must not skip the others
            logger.warning("worker_join_failed", task=worker.task, error=str(e))
            continue
        if not ended:
            logger.warning("worker_still_running_after_stop", task=worker.task, timeout_s=timeout_s)
