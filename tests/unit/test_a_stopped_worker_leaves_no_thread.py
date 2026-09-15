"""A background worker's thread ends when the worker is stopped (#1435).

The GC and health-check workers slept through their whole interval, 30s and
60s, between cycles, so a stopped worker's thread lived on until the sleep
ended, and nothing waited for it. The config reload poller did the same. Each
now waits on an event `stop()` sets, and `join()` waits for the thread to end.
`stop_background_workers`, which `ApplicationContext.shutdown()` calls, does
both for every worker, under `serve` and under the `Hangar` facade.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from mcp_hangar.gc import BackgroundWorker, ConfigReloadWorker, MetricsSnapshotWorker
from mcp_hangar.server.bootstrap.workers import stop_background_workers

#: Far longer than any wait below, so a thread that ends did not end by its interval running out.
LONG_INTERVAL_S = 3600
#: How long a stopped thread may take to end.
PROMPT_S = 5.0


def _stop_and_time(worker) -> float:
    started = time.monotonic()
    worker.stop()
    assert worker.join(PROMPT_S) is True
    return time.monotonic() - started


class TestEachWorkerEndsItsThreadOnStop:
    @pytest.mark.parametrize("task", ["gc", "health_check"])
    def test_the_gc_and_health_check_workers(self, task):
        worker = BackgroundWorker({}, interval_s=LONG_INTERVAL_S, task=task)
        worker.start()
        assert worker.thread.is_alive()

        assert _stop_and_time(worker) < PROMPT_S
        assert not worker.thread.is_alive()

    def test_the_metrics_snapshot_worker_and_its_second_start(self):
        worker = MetricsSnapshotWorker(interval_s=LONG_INTERVAL_S)
        worker.start()
        worker.start()  # a second start does nothing, rather than raise on the started thread

        assert _stop_and_time(worker) < PROMPT_S
        assert not worker.thread.is_alive()

    def test_the_config_reload_poller(self, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text("mcp_servers: {}\n")
        worker = ConfigReloadWorker(str(config), MagicMock(), interval_s=LONG_INTERVAL_S, use_watchdog=False)
        worker.start()
        assert worker.thread is not None and worker.thread.is_alive()

        assert _stop_and_time(worker) < PROMPT_S
        assert not worker.thread.is_alive()

    def test_a_worker_that_never_started_has_nothing_to_wait_for(self):
        assert BackgroundWorker({}, interval_s=LONG_INTERVAL_S).join(0) is True
        assert MetricsSnapshotWorker(interval_s=LONG_INTERVAL_S).join(0) is True
        assert ConfigReloadWorker(None, MagicMock()).join(0) is True


class _Worker:
    """A worker that writes what is done to it into a shared journal."""

    def __init__(self, task: str, journal: list[str], *, ends: bool = True, stop_fails: bool = False) -> None:
        self.task = task
        self.journal = journal
        self.ends = ends
        self.stop_fails = stop_fails
        self.timeouts: list[float] = []

    def stop(self) -> None:
        self.journal.append(f"stop:{self.task}")
        if self.stop_fails:
            raise RuntimeError("no stop")

    def join(self, timeout_s: float) -> bool:
        self.journal.append(f"join:{self.task}")
        self.timeouts.append(timeout_s)
        return self.ends


class TestStopBackgroundWorkers:
    def test_every_worker_is_told_to_stop_before_any_is_waited_on(self):
        journal: list[str] = []

        stop_background_workers([_Worker("gc", journal), _Worker("health_check", journal)])

        assert journal == ["stop:gc", "stop:health_check", "join:gc", "join:health_check"]

    def test_a_worker_that_fails_to_stop_does_not_keep_the_others_running(self):
        journal: list[str] = []
        real = BackgroundWorker({}, interval_s=LONG_INTERVAL_S, task="gc")
        real.start()

        stop_background_workers([_Worker("config_reload", journal, stop_fails=True), real], timeout_s=PROMPT_S)

        assert not real.thread.is_alive()
        assert journal == ["stop:config_reload", "join:config_reload"]

    def test_the_wait_is_bounded_for_all_of_them_together(self):
        journal: list[str] = []
        stuck = [_Worker("gc", journal, ends=False), _Worker("health_check", journal, ends=False)]

        stop_background_workers(stuck, timeout_s=0.5)

        timeouts = [timeout for worker in stuck for timeout in worker.timeouts]
        assert len(timeouts) == 2
        assert all(0.0 <= timeout <= 0.5 for timeout in timeouts)
        assert timeouts[1] <= timeouts[0]
