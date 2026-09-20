"""The facade starts coordination and the front-door warm-up `serve` starts, and starts once (#1465).

`ServerLifecycle.start` starts the management lease keeper and the event
tailer, and warms a front door's catalogue and retries its required servers.
The facade started none of them, so an embedded gateway with a
`coordination:` block never took the lease, and an embedded front door listed
nothing until each server had been started some other way. Two concurrent
`start()` calls could also both bootstrap. Both now start them through the
functions `ServerLifecycle` uses, and `start()` is serialised.

Each mode runs ``_facade_coordination_harness.py`` in a fresh interpreter on the
real ``bootstrap()``: ``coordination`` boots ``Hangar.from_config`` with a
``coordination:`` block, ``front_door`` boots ``SyncHangar.from_config`` in
front-door mode with a required catalogue. The unit tests are in
``tests/unit/test_facade.py``.
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

HARNESS = Path(__file__).with_name("_facade_coordination_harness.py")
MODES = ("coordination", "front_door")


def _run(mode: str, tmp: Path) -> dict[str, Any]:
    out = tmp / mode / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(out)],
        capture_output=True,
        text=True,
        timeout=120,
        env=dict(os.environ),
    )
    assert result.returncode == 0 and out.exists(), (
        f"{mode}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return dict(json.loads(out.read_text()))


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("facade-coordination")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


@pytest.mark.parametrize("mode", MODES)
def test_two_concurrent_starts_bootstrap_once(runs, mode) -> None:
    assert runs[mode]["boots"] == 1


def test_the_lease_is_taken_and_renewed(runs) -> None:
    run = runs["coordination"]

    assert run["acquired"] is True
    assert run["renewed"] is True
    assert run["may_manage"] is True


def test_the_event_tailer_follows_the_log(runs) -> None:
    run = runs["coordination"]

    assert run["tailed"] is True
    assert run["threads_while_started"] == ["event-tailer", "management-lease"]


def test_stop_releases_the_lease_and_leaves_no_coordination_thread(runs) -> None:
    run = runs["coordination"]

    assert run["lease_after_stop"] is None
    assert run["threads_after_stop"] == []


def test_a_front_door_is_warmed_at_start(runs) -> None:
    run = runs["front_door"]

    assert run["warmed"] is True
    assert run["detail"]["missing"] == ["down"]


def test_the_required_catalogue_retry_runs_and_stop_stops_it(runs) -> None:
    run = runs["front_door"]

    assert run["retry_running"] is True
    assert run["retry_after_stop"] == "stopped"
    assert run["warm_up_threads_after_stop"] == []


@pytest.mark.parametrize("mode", MODES)
def test_stop_leaves_no_thread_the_facade_started(runs, mode) -> None:
    assert runs[mode]["left_running"] == []
