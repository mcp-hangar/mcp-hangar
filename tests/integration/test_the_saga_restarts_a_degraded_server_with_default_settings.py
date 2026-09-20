"""With default settings, every restart the recovery saga schedules runs (#1401).

The saga restarts a degraded server 5s, then 10s, after each degrade. The
server's own backoff after three failures is about 8s, then about 16s. So the
server refused the saga's first restart, and nothing scheduled another: the
saga never restarted anything, and never reached its give-up.

``_dead_server_harness.py defaults`` runs the real ``bootstrap()``, the served
app, the health and GC workers and the sagas it registered, with every health
and saga setting at its default except ``max_retries``, which is 2 instead of
3. A third restart waits out a 32s backoff, and the run would outlast the
job's 60s timeout. The upstream breaks and stays broken.

What this pins:

- The saga's first restart comes on its own default backoff, 5s after the
  degrade, and the server refuses it: its backoff has not elapsed.
- A refused restart is tried again, and each attempt then runs: every restart
  the saga's budget allows reaches a real start of the upstream process.
- The refusals are not counted: the saga gives up after ``max_retries`` real
  attempts, not after its first refusals, and the server reads ``dead``.
- Nothing starts it after the give-up.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_dead_server_harness.py")
# As `_dead_server_harness.py` names them.
SERVER = "svc-d"
MAX_RETRIES = 2
#: The saga's default first backoff.
SAGA_FIRST_BACKOFF_S = 5.0


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("saga-defaults") / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), "defaults", str(out)],
        capture_output=True,
        text=True,
        timeout=55,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    return json.loads(out.read_text())


def _timeline(run: dict[str, Any]) -> list[tuple[str, float]]:
    return [(what, at) for server, what, at in run["timeline"] if server == SERVER]


def _until_given_up(run: dict[str, Any]) -> list[tuple[str, float]]:
    """Degrades and start commands after the upstream broke, up to the give-up."""
    timeline = _timeline(run)
    [given_up_at] = [at for what, at in timeline if what == "given_up"]
    first_degrade = min(at for what, at in timeline if what == "degraded")
    return [(what, at) for what, at in timeline if first_degrade <= at <= given_up_at and what != "given_up"]


def _attempts(run: dict[str, Any]) -> list[list[str]]:
    """The saga's start commands, one list per attempt: refusals, then the start that ran."""
    attempts: list[list[str]] = []
    current: list[str] = []
    for what, _ in _until_given_up(run):
        if what == "degraded":
            continue
        current.append(what)
        if what != "refused":
            attempts.append(current)
            current = []
    assert current == [], f"refusals the saga dropped without trying again: {current}"
    return attempts


def test_the_first_restart_comes_on_the_sagas_default_backoff(run):
    timeline = _until_given_up(run)
    degraded_at = timeline[0][1]
    first_start_at = next(at for what, at in timeline if what != "degraded")

    assert timeline[0][0] == "degraded"
    assert first_start_at - degraded_at == pytest.approx(SAGA_FIRST_BACKOFF_S, abs=1.0)


def test_every_restart_the_saga_schedules_runs(run):
    attempts = _attempts(run)

    # Each ended in a real start, which failed: the upstream is broken.
    assert [attempt[-1] for attempt in attempts] == ["failed"] * MAX_RETRIES, attempts
    restarts = [e for e in run["events"][run["broken_mark"] : run["dead_mark"]] if e[1:3] == ["state", "initializing"]]
    assert len(restarts) == MAX_RETRIES, "a start command that did not reach a start of the upstream"


def test_the_server_refused_each_restart_before_its_backoff_ran_out(run):
    # The saga's 5s and 10s are shorter than the server's ~8s and ~16s, so each
    # attempt was refused first and tried again. Before #1401 the first refusal
    # was the end of recovery.
    attempts = _attempts(run)

    assert all(attempt[0] == "refused" for attempt in attempts), attempts


def test_the_saga_gives_up_after_its_budget_and_the_server_reads_dead(run):
    dead = run["dead"]

    # Recorded as a stop with its own reason, right before the move to dead (#1360).
    stop_at = run["events"].index([SERVER, "stopped", "max_retries_exceeded"])
    assert run["events"][stop_at + 1] == [SERVER, "state", "dead", "given_up"]
    assert (dead["domain_state"], dead["state"], dead["up"]) == ("dead", 4.0, 0.0)
    assert dead["stops"] == {"max_retries_exceeded": 1.0, "shutdown": 0.0}


def test_nothing_starts_it_after_the_give_up(run):
    timeline = _timeline(run)
    [given_up_at] = [at for what, at in timeline if what == "given_up"]

    assert [what for what, at in timeline if at > given_up_at and what != "degraded"] == []
    assert run["after_quiet"]["domain_state"] == "dead"
