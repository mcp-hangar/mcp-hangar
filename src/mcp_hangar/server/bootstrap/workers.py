"""Background workers: built by bootstrap, started and stopped here."""

import time
from collections.abc import Callable, Sequence
from typing import Any, cast

from ...application.read_models.tool_projection import add_change_listener, remove_change_listener
from ...gc import BackgroundWorker, MetricsSnapshotWorker
from ...group_recovery import GroupRecoveryWorker
from ...logging_config import get_logger
from ...pin_recheck import PIN_RECHECK_DEFAULT_INTERVAL_S, PinRecheckWorker, pin_recheck_interval_s
from ..state import GROUPS, get_runtime
from .coordination import may_manage

logger = get_logger(__name__)

GC_WORKER_INTERVAL_SECONDS = 30
"""Interval for garbage collection worker."""

HEALTH_CHECK_INTERVAL_SECONDS = 60
"""Interval for health check worker."""

METRICS_SNAPSHOT_INTERVAL_SECONDS = 60
"""Interval for metrics history snapshot worker."""

GROUP_RECOVERY_INTERVAL_SECONDS = 30
"""Interval for the worker that starts the members of a group with none to select (#1565)."""

WORKER_STOP_TIMEOUT_SECONDS = 10.0
"""How long `stop_background_workers` waits, in total, for the workers' threads to end."""


#: The pin re-check's drift report, installed as a projection change listener;
#: replaced, not added to, by each `create_background_workers`.
_drift_listener: Callable[[], None] | None = None


def create_background_workers(
    config: dict[str, Any] | None = None,
) -> list[BackgroundWorker | MetricsSnapshotWorker | GroupRecoveryWorker | PinRecheckWorker]:
    """Create (but don't start) background workers.

    Args:
        config: Optional full application config dict.

    Returns:
        List of worker instances (not started).

    Raises:
        ValueError: ``tool_projection.pin_recheck_interval_s`` is out of range.
    """
    # Read first, so a bad value refuses the boot before anything is built.
    pin_recheck_s = pin_recheck_interval_s(config)
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

    # Per replica, as GC and health checks are: it starts this replica's own
    # members. `GROUPS` is the live mapping a reload updates in place.
    group_recovery_worker = GroupRecoveryWorker(GROUPS, interval_s=GROUP_RECOVERY_INTERVAL_SECONDS)

    workers: list[Any] = [gc_worker, health_worker, metrics_worker, group_recovery_worker]

    # Per replica too: each re-lists the catalogue it serves from (#1693). Built
    # whenever it is on, pins or not -- a reload can add the first pin.
    # A drifted tool is withheld from every listing, so its call no longer
    # reports the drift on the front door: a change listener does, whether or
    # not the re-check is on, sharing what was reported with it (#1776).
    # Built, but neither listed nor started, when the interval is 0 (#1776).
    pin_recheck = PinRecheckWorker(mcp_servers, GROUPS, interval_s=pin_recheck_s or PIN_RECHECK_DEFAULT_INTERVAL_S)
    global _drift_listener
    if _drift_listener is not None:
        remove_change_listener(_drift_listener)
    _drift_listener = pin_recheck.report_drift
    add_change_listener(_drift_listener)
    if pin_recheck_s > 0:
        workers.append(pin_recheck)
    worker_names = [worker.task for worker in workers]

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
