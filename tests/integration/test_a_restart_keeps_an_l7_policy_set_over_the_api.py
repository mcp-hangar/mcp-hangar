"""A restart keeps an L7 egress policy set over the API on a server config.yaml declares (#1306).

The operator pushes its compiled policy over ``POST
/api/mcp_servers/{id}/l7_policy``, and the push stores it on the server's fleet
row. At startup, recovery skipped the row of every server the file already
declares, so it never read the policy back: after a rollout the gateway served
calls the policy denies until the operator's next reconcile -- 2h16m in the
report -- while the CR still read ``Enforce``.

``_restart_l7_policy_harness.py`` runs twice, each time in a fresh interpreter
over the same SQLite data directory: the real ``bootstrap()`` from a file, a
real stdio subprocess server, the policy over REST, and the calls through
``hangar_call`` over streamable HTTP. The unit-level versions are in
``tests/unit/test_recovery_service.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# Six gateways, one after the other, with one real upstream each.
pytestmark = pytest.mark.timeout(300)

HARNESS = Path(__file__).with_name("_restart_l7_policy_harness.py")
DENIED = "EgressPolicyDeniedError"


def _phase(workdir: Path, phase: str) -> dict[str, Any]:
    out = workdir / f"{phase}.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(workdir), phase, str(out)],
        capture_output=True,
        text=True,
        timeout=80,
    )
    assert result.returncode == 0 and out.exists(), f"{phase} exited {result.returncode}:\n{result.stderr[-6000:]}"
    return dict(json.loads(out.read_text()))


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    workdir = tmp_path_factory.mktemp("restart-l7")
    phases = {phase: _phase(workdir, phase) for phase in ("before", "after", "clear", "after_clear")}
    memory = tmp_path_factory.mktemp("memory-l7")
    phases["memory"] = _phase(memory, "memory")
    phases["memory_after"] = _phase(memory, "memory_after")
    return phases


def test_the_policy_was_in_force_before_the_restart(run: dict[str, Any]) -> None:
    # Otherwise a refusal after the restart would say nothing.
    before = run["before"]

    assert before["unset"] == {"policy": 404, "call": "served"}
    assert before["set"]["status"] == 200
    # SQLite, and recovery on by default: the push says a restart keeps it.
    assert before["set"]["body"]["persisted"] is True
    assert before["call"] == DENIED


@pytest.mark.security
def test_the_policy_survives_the_restart_and_still_refuses(run: dict[str, Any]) -> None:
    after = run["after"]

    assert after["policy"] != 404, "the restarted gateway holds no policy for a server the file declares"
    assert after["policy"]["tools"]["deny"] == ["add"]
    assert after["call"] == DENIED


def test_a_policy_cleared_before_the_restart_stays_cleared(run: dict[str, Any]) -> None:
    # The restored policy comes from the row, so the row has to follow a DELETE
    # too; otherwise a restart would bring back a policy the operator removed.
    assert run["clear"]["cleared"] == 200
    assert run["clear"]["policy"] == 404
    assert run["after_clear"]["policy"] == 404
    assert run["after_clear"]["call"] == "served"


def test_without_a_persistence_backend_the_push_says_a_restart_drops_it(run: dict[str, Any]) -> None:
    # The chart default. The policy is enforced now, but nothing keeps it, and
    # the push is where the operator learns that.
    memory = run["memory"]

    assert memory["set"]["status"] == 200
    assert memory["set"]["body"]["persisted"] is False
    assert memory["call"] == DENIED


class TestTheScrapeSaysWhichPolicyTheReplicaHolds:
    """``mcp_hangar_l7_policy_held`` and its timestamp, read from ``GET /metrics`` (#1562)."""

    def test_a_push_shows_as_held(self, run: dict[str, Any]) -> None:
        scrape = run["before"]["metrics"]

        assert scrape["held"] == {"math": ["Enforce", 1.0]}
        assert "math" in scrape["last_set"]

    def test_a_policy_restored_at_startup_shows_as_held_and_stamped(self, run: dict[str, Any]) -> None:
        # Recovery installing the stored policy is a delivery from this
        # replica's point of view, so it stamps the time too.
        before, after = run["before"]["metrics"], run["after"]["metrics"]

        assert after["held"] == {"math": ["Enforce", 1.0]}
        assert after["last_set"]["math"] > before["last_set"]["math"]

    def test_a_clear_drops_the_held_series(self, run: dict[str, Any]) -> None:
        assert run["clear"]["metrics"]["held"] == {}
        assert run["clear"]["metrics"]["last_set"]["math"] > run["after"]["metrics"]["last_set"]["math"]
        assert run["after_clear"]["metrics"]["held"] == {}

    @pytest.mark.security
    def test_a_restart_without_a_durable_backend_shows_no_held_series(self, run: dict[str, Any]) -> None:
        # The gap #1560 could not close: memory-only starts empty. The metric
        # must make that observable -- the family is there, the series is not.
        assert run["memory"]["metrics"]["held"] == {"math": ["Enforce", 1.0]}
        after = run["memory_after"]
        assert after["metrics"]["family"] is True
        assert after["metrics"]["held"] == {}
        assert after["policy"] == 404
