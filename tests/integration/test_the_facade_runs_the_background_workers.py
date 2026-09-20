"""The facade runs the GC and health-check workers `serve` runs, and stops them (#1435).

`bootstrap()` builds the background workers and `ServerLifecycle.start` starts
them under `serve`. The facade never did, so an embedded gateway kept every
server it had called running for the life of the host and never health
checked one. Both now start them through `start_background_workers`, and
`ApplicationContext.shutdown()` stops them and waits for their threads.

Each mode runs ``_facade_workers_harness.py`` in a fresh interpreter on the real
``bootstrap()``, with the worker intervals lowered to a second: ``sync`` boots
``SyncHangar.from_builder``, ``async`` boots ``Hangar.from_config`` on a file.
The unit tests, including the one that fails when `serve` and the facade start
different workers, are in ``tests/unit/test_facade.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_facade_workers_harness.py")
MODES = ("sync", "async")


def _run(mode: str, tmp: Path) -> dict[str, Any]:
    out = tmp / mode / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(out)],
        capture_output=True,
        text=True,
        timeout=90,
        env=dict(os.environ),
    )
    assert result.returncode == 0 and out.exists(), (
        f"{mode}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return dict(json.loads(out.read_text()))


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("facade-workers")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


@pytest.mark.parametrize("mode", MODES)
def test_the_facade_runs_every_worker_bootstrap_built(runs, mode) -> None:
    run = runs[mode]

    assert {"gc", "health_check", "metrics_snapshot"} <= set(run["enabled"])
    assert run["running"] == run["enabled"]


def test_a_config_file_runs_its_reload_worker_too(runs) -> None:
    assert "config_reload" in runs["async"]["running"]


@pytest.mark.parametrize("mode", MODES)
def test_a_second_start_starts_no_second_set(runs, mode) -> None:
    run = runs[mode]

    assert run["threads_after_start"]
    assert run["threads_after_second_start"] == run["threads_after_start"]


@pytest.mark.parametrize("mode", MODES)
def test_an_idle_server_is_stopped_by_the_gc_worker(runs, mode) -> None:
    run = runs[mode]

    assert ["idle", "idle"] in run["stops"]
    assert run["idle_state"] == "cold"
    assert ["steady", "idle"] not in run["stops"]


@pytest.mark.parametrize("mode", MODES)
def test_a_health_check_runs_on_schedule(runs, mode) -> None:
    assert runs[mode]["health_checks"].get("steady", 0) >= 1


@pytest.mark.parametrize("mode", MODES)
def test_stop_leaves_no_worker_thread_running(runs, mode) -> None:
    run = runs[mode]

    assert run["running_after_stop"] == []
    assert run["worker_threads_after_stop"] == []


@pytest.mark.parametrize("mode", MODES)
def test_stop_leaves_no_thread_the_facade_started(runs, mode) -> None:
    assert runs[mode]["left_running"] == []
