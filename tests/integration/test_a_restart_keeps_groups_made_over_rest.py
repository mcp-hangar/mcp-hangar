"""A restart reaches what a reload keeps: groups and members made over REST come back (#2172).

A reload has kept them since #2169. A restart went back to the file's groups, so
a server added to a group over REST came back from its own row outside that
group, and group policy no longer applied to it.

``_reload_group_harness.py`` runs each phase in a process of its own on one data
directory: the real ``bootstrap()`` from a file, REST and MCP calls.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(200)

HARNESS = Path(__file__).with_name("_reload_group_harness.py")
DENIED = "ToolAccessDeniedError"
PHASES = ("made", "restarted", "file_takes", "again", "file_drops")
RUNTIME = {
    "strategy": "weighted_round_robin",
    "min_healthy": 2,
    "description": "from REST",
    "members": {"runtime-member": [1, 1]},
}


def _phases(workdir: Path, backend: str, phases: tuple[str, ...]) -> dict[str, Any]:
    outcomes: dict[str, Any] = {}
    for phase in phases:
        out = workdir / f"{phase}.json"
        result = subprocess.run(
            [sys.executable, str(HARNESS), f"restart:{backend}:{phase}", str(workdir), str(out)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0 and out.exists(), f"{phase} exited {result.returncode}:\n{result.stderr[-6000:]}"
        outcomes[phase] = json.loads(out.read_text())
    return outcomes


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _phases(tmp_path_factory.mktemp("restart-groups"), "sqlite", PHASES)


@pytest.fixture(scope="module")
def memory(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _phases(tmp_path_factory.mktemp("restart-groups-memory"), "memory", PHASES[:2])


@pytest.fixture(scope="module")
def front_door(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _phases(tmp_path_factory.mktemp("restart-groups-front-door"), "front_door", PHASES[:2])


def test_a_group_made_over_rest_is_back_with_its_strategy_members_and_policy(run: dict[str, Any]) -> None:
    restarted = run["restarted"]
    assert restarted["at_boot"]["runtime"] == RUNTIME
    assert restarted["policy"] == ["add"]


def test_a_member_of_a_file_group_is_back_with_its_weight_and_priority(run: dict[str, Any]) -> None:
    assert run["restarted"]["at_boot"]["pool"]["members"]["kept-member"] == [3, 2]


def test_a_refused_tool_is_refused_from_the_first_call_after_a_restart(run: dict[str, Any]) -> None:
    assert run["restarted"]["first_call"] == DENIED


def test_group_policy_applies_before_anything_is_served(run: dict[str, Any]) -> None:
    restarted = run["restarted"]
    assert restarted["workers_running"] == []
    assert restarted["governed_at_boot"]["new-server"] == ["add"]
    assert restarted["governed_at_boot"]["kept-member"] == ["add"]
    assert restarted["governed_at_boot"]["runtime-member"] == ["add"]


def test_a_call_through_a_restored_group_is_served(run: dict[str, Any]) -> None:
    assert run["restarted"]["through_the_group"] == "served"


def test_a_group_deleted_over_rest_stays_deleted(run: dict[str, Any]) -> None:
    assert "deleted" not in run["restarted"]["at_boot"]
    assert "in-deleted" in run["restarted"]["ids"]


def test_a_member_removed_over_rest_stays_removed(run: dict[str, Any]) -> None:
    assert "removed" not in run["restarted"]["at_boot"]["runtime"]["members"]
    assert "removed" in run["restarted"]["ids"]


def test_a_stored_member_whose_server_is_gone_leaves_no_member(run: dict[str, Any]) -> None:
    assert "gone-member" not in run["restarted"]["ids"]
    assert "gone-member" not in run["restarted"]["at_boot"]["runtime"]["members"]


def test_the_state_after_a_restart_is_the_state_after_a_reload(run: dict[str, Any]) -> None:
    assert run["made"]["status"] == 200
    assert run["restarted"]["at_boot"] == run["made"]["groups"]


def test_a_reload_after_a_restart_changes_nothing(run: dict[str, Any]) -> None:
    restarted = run["restarted"]
    assert restarted["reloaded"] == {"status": 200, "groups": restarted["at_boot"]}


def test_the_file_wins_after_a_restart_for_what_it_now_declares(run: dict[str, Any]) -> None:
    taken = run["file_takes"]["at_boot"]
    assert taken["pool"]["members"]["kept-member"] == [7, 1]
    assert taken["runtime"]["strategy"] == "random"
    assert taken["runtime"]["members"] == RUNTIME["members"]
    assert run["file_takes"]["reloaded"]["groups"] == taken


def test_a_further_restart_changes_nothing(run: dict[str, Any]) -> None:
    assert run["again"]["at_boot"] == run["file_takes"]["at_boot"]
    assert run["again"]["reloaded"]["groups"] == run["again"]["at_boot"]


def test_a_row_outlives_the_file_taking_over_what_it_records(run: dict[str, Any]) -> None:
    """A known gap, pinned so that closing it is seen: the state a reload and a restart disagree on.

    A row written before the file named its group or member is still there when
    the file drops it again, so a restart restores it where a reload would
    remove it. Rows are shared and a reload is local, as for a server's row.
    """
    dropped = run["file_drops"]["at_boot"]
    assert dropped["pool"]["members"]["kept-member"] == [3, 2]
    assert dropped["runtime"] == RUNTIME


def test_the_front_door_withholds_a_refused_tool_from_the_first_listing(front_door: dict[str, Any]) -> None:
    restarted = front_door["restarted"]
    assert restarted["at_boot"]["pool"]["members"]["new-server"] == [1, 1]
    assert restarted["first_listing_has_add"] is False
    assert restarted["flat_add_refused"] is True
    assert restarted["other_tools_listed"] is True


def test_the_in_memory_backend_has_the_files_groups_after_a_restart(memory: dict[str, Any]) -> None:
    assert "runtime" in memory["made"]["groups"]
    assert memory["restarted"]["at_boot"] == {
        "pool": {
            "strategy": "round_robin",
            "min_healthy": 1,
            "description": None,
            "members": {"file-member": [1, 1]},
        }
    }
