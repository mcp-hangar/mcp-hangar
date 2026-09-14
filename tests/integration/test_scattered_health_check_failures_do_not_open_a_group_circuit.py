"""Scattered health-check failures do not open a group's circuit, on the wiring ``serve --http`` runs (#1390).

Runs ``_group_recovery_harness.py scattered`` in a fresh interpreter: the real
``bootstrap()``, the health-check worker it created, the ``GroupRebalanceSaga``
it registered, and ``McpServerGroup``. The upstream is ``tests/mock_provider.py``.
Its ``tools/list``, which is what a health check sends, fails while a flag file
exists, and the harness sets the flag for each check in turn.

The group's circuit opens at three failures. The member fails two checks,
passes one, and does that twice more: six failures, never three in a row.
Before #1390 a success never reset the count on a closed circuit, so the third
failure of the run opened it, at the fourth check. The run ends with a third
failure in a row, which does open it, so the count is live, not ignored.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_group_recovery_harness.py")

# As `_group_recovery_harness.py` orders them.
PATTERN = ["fail", "fail", "pass", "fail", "fail", "pass", "fail", "fail", "fail"]
THRESHOLD = 3


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("group-scattered") / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), "scattered", str(out)],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    return json.loads(out.read_text())


def test_the_health_worker_ran_every_check_in_order(run):
    assert run["pattern"] == PATTERN
    assert [check["passed"] for check in run["checks"]] == [outcome == "pass" for outcome in PATTERN], run["checks"]


def test_scattered_failures_never_open_the_circuit(run):
    scattered = run["checks"][:-1]

    assert sum(1 for outcome in PATTERN[:-1] if outcome == "fail") > THRESHOLD
    assert all(check["circuit_open"] is False for check in scattered), run["checks"]
    assert all(check["in_rotation"] is True for check in scattered), run["checks"]


def test_a_passing_check_starts_the_count_again(run):
    assert [check["circuit_failures"] for check in run["checks"][:-1]] == [1, 2, 0, 1, 2, 0, 1, 2], run["checks"]


def test_the_third_failure_in_a_row_opens_the_circuit(run):
    last = run["checks"][-1]

    assert last["circuit_open"] is True and last["circuit_failures"] == THRESHOLD, run["checks"]


def test_nothing_called_rebalance(run):
    """``rebalance()`` resets the count too; the point is that a success did."""
    assert run["rebalances"] == []
