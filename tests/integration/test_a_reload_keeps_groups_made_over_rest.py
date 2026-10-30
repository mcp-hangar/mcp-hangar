"""A reload keeps the groups and members made over REST, so group policy goes on applying (#2169).

A reload built every group from the file alone and dropped any other, so a
member added with ``POST /api/groups/{id}/members`` stopped being a member. The
policy of its group stopped applying to it, on a reload, with no sign of it.

``_reload_group_harness.py`` runs in a fresh interpreter: the real
``bootstrap()`` from a file, real stdio subprocess servers, REST and MCP calls.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(120)

HARNESS = Path(__file__).with_name("_reload_group_harness.py")
DENIED = "ToolAccessDeniedError"


def _run(workdir: Path, mode: str) -> dict[str, Any]:
    out = workdir / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(workdir), str(out)],
        capture_output=True,
        text=True,
        timeout=110,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-6000:]}"
    return dict(json.loads(out.read_text()))


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run(tmp_path_factory.mktemp("reload-groups"), "egress")


@pytest.fixture(scope="module")
def front_door(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run(tmp_path_factory.mktemp("reload-groups-front-door"), "front_door")


def test_the_scenario_starts_with_group_policy_applying(run: dict[str, Any]) -> None:
    assert run["before"]["surface"]["kept-member"] == DENIED
    assert run["before"]["surface"]["runtime-member"] == DENIED
    assert "kept-member" in run["before"]["groups"]["pool"]["members"]


def test_a_member_added_over_rest_is_still_governed_by_its_group_after_a_reload(run: dict[str, Any]) -> None:
    assert run["status"] == 200
    assert run["surface"]["kept-member"] == DENIED
    assert run["groups"]["pool"]["members"]["kept-member"] == {"weight": 3, "holds_the_running_server": True}


def test_a_group_created_over_rest_stays_with_its_strategy_members_and_policy(run: dict[str, Any]) -> None:
    runtime = run["groups"]["runtime"]
    assert runtime["strategy"] == "priority"
    assert "runtime-member" in runtime["members"]
    assert run["group_policy"] == ["add"]
    assert run["surface"]["runtime-member"] == DENIED
    assert run["surface"]["runtime"] == DENIED


def test_a_group_policy_also_applies_to_a_call_naming_the_group(run: dict[str, Any]) -> None:
    assert run["surface"]["pool"] == DENIED


def test_the_kept_members_are_route_candidates(run: dict[str, Any]) -> None:
    assert "kept-member" in run["groups"]["pool"]["route_candidates"]
    assert "runtime-member" in run["groups"]["runtime"]["route_candidates"]


def test_the_servers_of_kept_members_were_not_touched(run: dict[str, Any]) -> None:
    assert run["same_server"]["kept-member"] is True
    assert run["running"]["kept-member"] is True
    assert run["same_server"]["runtime-member"] is True
    assert run["running"]["runtime-member"] is True


def test_the_file_wins_for_a_member_it_now_declares(run: dict[str, Any]) -> None:
    assert run["groups"]["pool"]["members"]["declared-later"]["weight"] == 5


def test_a_member_the_file_removes_while_declaring_it_itself_goes(run: dict[str, Any]) -> None:
    assert "was-a-file-member" in run["before"]["groups"]["pool"]["members"]
    assert "was-a-file-member" not in run["groups"]["pool"]["members"]
    assert "was-a-file-member" in run["ids"]


def test_a_member_whose_server_left_the_fleet_leaves_its_group(run: dict[str, Any]) -> None:
    assert "leaves" not in run["ids"]
    assert "leaves" not in run["groups"]["runtime"]["members"]


def test_a_member_of_a_kept_group_follows_a_server_the_reload_rebuilt(run: dict[str, Any]) -> None:
    assert run["groups"]["runtime"]["members"]["edited"]["holds_the_running_server"] is True


def test_a_group_the_file_dropped_is_removed_and_the_member_added_over_rest_keeps_running(
    run: dict[str, Any],
) -> None:
    assert "doomed" in run["before"]["groups"]
    assert "doomed" not in run["groups"]
    assert "doomed-member" in run["ids"]
    assert run["running"]["doomed-member"] is True


def test_every_member_holds_the_server_in_the_fleet(run: dict[str, Any]) -> None:
    for group in run["groups"].values():
        assert all(member["holds_the_running_server"] for member in group["members"].values()), group


def test_a_second_reload_changes_nothing(run: dict[str, Any]) -> None:
    assert run["second_reload"] == {"status": 200, "groups": run["groups"]}


def test_the_front_door_answers_alike_before_and_after_a_reload(front_door: dict[str, Any]) -> None:
    assert front_door["status"] == 200
    assert front_door["surface"] == front_door["before"]["surface"]
    assert front_door["surface"] == {"listed_add": False, "flat_add_refused": True}
    assert "kept-member" in front_door["groups"]["pool"]["members"]
    assert "kept-member" in front_door["groups"]["pool"]["route_candidates"]
