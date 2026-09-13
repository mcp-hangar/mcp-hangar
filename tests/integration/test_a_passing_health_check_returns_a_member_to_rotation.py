"""A group heals itself from real failures, and idle reaping is not one, on the wiring ``serve --http`` runs (#1355).

Each mode runs ``_group_recovery_harness.py`` in a fresh interpreter: the real
``bootstrap()``, ``hangar_call`` through the served app, and the background
workers ``bootstrap()`` created. Nothing here builds a saga or registers a
member with one. The path under test is the one a replica takes: an event from
a worker, the event bus, the ``GroupRebalanceSaga`` that ``bootstrap()``
registered, and ``McpServerGroup``.

That path was cut in two places, and a test that builds the saga itself sees
neither. ``bootstrap()`` loads the servers before it creates the saga, so
``_load_group_members`` found no saga to tell which group a member is in. And
the saga was handed ``ctx.groups`` while that was still the context's own empty
dict rather than ``GROUPS``. Every ``HealthCheckPassed`` was dropped at the
lookup, so a member driven out stayed out. In the live run, five minutes and
seven passing checks later, every member still had ``consecutive_failures: 2``
and the group had ``circuit_open: True``. A third gap sat behind those two: a
member back in rotation did not close the group's circuit. Only
``rebalance()`` did that.

Joining the path up made every event the saga handles reach the group, and the
saga used to count a stop of any kind as a member failure. The GC reaps idle
members as a matter of course, so the ``idle`` mode pins that a reap is not a
failure: with the strictest thresholds a group takes, a member reaped and
started again on demand never leaves rotation, and the circuit never opens.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_group_recovery_harness.py")
RECOVERY_MODES = ("single", "pair")
MODES = (*RECOVERY_MODES, "idle")

# As `_group_recovery_harness.py` names and counts them.
RECOVERING, STILL_DOWN = "math-a", "math-b"
IDLE_MEMBERS, CYCLES = ("math-a", "math-b"), 3


def _run(mode: str, tmp: Path) -> dict[str, Any]:
    out = tmp / mode / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(out)],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{mode}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return json.loads(out.read_text())


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    # Concurrently, under the 60s pytest-timeout the integration job applies.
    tmp = tmp_path_factory.mktemp("group-recovery")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


def _member(status: dict[str, Any], member_id: str) -> dict[str, Any]:
    return next(m for m in status["members"] if m["id"] == member_id)


def _outcome(batch: dict[str, Any]) -> dict[str, Any]:
    [result] = batch["results"]
    return result


# ----------------------------------------------------------------------------
# Recovery: a member driven out by failures comes back on a passing check.
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("mode", RECOVERY_MODES)
def test_consecutive_failures_drive_every_member_out_and_the_group_refuses(runs, mode):
    """The state the live replica was stuck in, reached through the served call path."""
    run = runs[mode]
    assert _outcome(run["calls"]["before"])["success"] is True, run["calls"]["before"]

    tripped = run["status"]["tripped"]
    assert tripped["healthy_count"] == 0 and tripped["circuit_open"] is True, tripped
    assert all(m["in_rotation"] is False and m["consecutive_failures"] == 2 for m in tripped["members"]), tripped
    assert _outcome(run["calls"]["refused"])["error_type"] == "NoAvailableMemberError", run["calls"]["refused"]


@pytest.mark.parametrize("mode", RECOVERY_MODES)
def test_the_health_worker_published_a_passing_check_for_the_member(runs, mode):
    assert runs[mode]["health_checks_passed"].get(RECOVERING, 0) >= 1, runs[mode]["health_checks_passed"]


@pytest.mark.parametrize("mode", RECOVERY_MODES)
def test_a_passing_health_check_returns_the_member_to_rotation(runs, mode):
    after = runs[mode]["status"]["after"]
    member = _member(after, RECOVERING)

    assert member["in_rotation"] is True, after
    assert member["consecutive_failures"] == 0, after


@pytest.mark.parametrize("mode", RECOVERY_MODES)
def test_the_next_call_through_the_group_succeeds(runs, mode):
    outcome = _outcome(runs[mode]["calls"]["after"])

    assert outcome["success"] is True, outcome


@pytest.mark.parametrize("mode", MODES)
def test_recovery_did_not_come_from_rebalance(runs, mode):
    """``rebalance()`` would recover the group too; the point is that nothing had to call it."""
    assert runs[mode]["rebalances"] == []


def test_the_recovered_member_closes_the_circuit_of_its_group(runs):
    after = runs["single"]["status"]["after"]

    assert after["circuit_open"] is False, after
    assert after["state"] == "healthy" and after["is_available"] is True, after


def test_with_min_healthy_one_a_single_recovered_member_closes_the_circuit(runs):
    after = runs["pair"]["status"]["after"]

    # The other upstream is still down, and stays out.
    assert _member(after, STILL_DOWN)["in_rotation"] is False, after
    assert after["min_healthy"] == 1 and after["healthy_count"] == 1, after
    assert after["circuit_open"] is False, after
    assert after["state"] == "healthy" and after["is_available"] is True, after


# ----------------------------------------------------------------------------
# Idle reaping: a stop the gateway chose is not a member failure.
# ----------------------------------------------------------------------------


def test_the_gc_worker_reaped_every_member_in_every_cycle(runs):
    run = runs["idle"]

    assert [cycle["idle_stops"] for cycle in run["cycles"]] == [n * len(IDLE_MEMBERS) for n in range(1, CYCLES + 1)], (
        run["stops"]
    )
    assert {reason for _member_id, reason in run["stops"]} == {"idle"}, run["stops"]


def test_every_call_through_an_idle_reaped_group_succeeds(runs):
    for number, cycle in enumerate(runs["idle"]["cycles"], start=1):
        outcomes = [_outcome(call) for call in cycle["calls"]]
        assert all(o["success"] is True for o in outcomes), (number, outcomes)


def test_an_idle_reaped_member_never_leaves_rotation(runs):
    for number, cycle in enumerate(runs["idle"]["cycles"], start=1):
        status = cycle["status"]
        for member_id in IDLE_MEMBERS:
            member = _member(status, member_id)
            assert member["in_rotation"] is True and member["consecutive_failures"] == 0, (number, status)


def test_idle_reaping_never_opens_the_circuit(runs):
    for number, cycle in enumerate(runs["idle"]["cycles"], start=1):
        status = cycle["status"]
        assert status["circuit_open"] is False and status["healthy_count"] == len(IDLE_MEMBERS), (number, status)
