"""A group whose members are all cold reports none healthy, and a call through it still works (#1356).

Runs ``_cold_group_harness.py`` in a fresh interpreter: the real
``bootstrap()``, the served MCP app and REST API, the GC worker reaping idle
members, and ``hangar_call`` through the group.

`healthy_count` used to count every member in rotation that was not dead. A
group whose members the GC had reaped reported them all healthy with nothing
running. It now counts members that are `ready` and in rotation, and
`members_in_rotation_count` reports rotation size. Groups start members
lazily, so a group of cold members must still route: the call is what starts
one. And every surface that reports the group reports the same counts.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_cold_group_harness.py")
MEMBERS = ("math-a", "math-b")
MOMENTS = ("booted", "reaped", "called")
#: The surfaces that return the group's `to_status_dict()` as it is.
WHOLE = ("rest", "rest_list", "hangar_details", "hangar_group_list", "hangar_list")


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("cold-group") / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(out)],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    return json.loads(out.read_text())


def _counts(group: dict[str, Any]) -> tuple[int, int, int]:
    return group["healthy_count"], group["members_in_rotation_count"], group["total_members"]


def _states(group: dict[str, Any]) -> list[tuple[str, bool]]:
    return sorted((m["state"], m["in_rotation"]) for m in group["members"])


def test_both_members_are_healthy_after_boot(run):
    assert _counts(run["booted"]["rest"]) == (2, 2, 2), run["booted"]["rest"]


def test_the_gc_reaped_both_members(run):
    assert sorted(run["stops"]) == [[member, "idle"] for member in MEMBERS], run["stops"]


def test_a_group_of_cold_members_reports_none_healthy_and_both_in_rotation(run):
    group = run["reaped"]["rest"]

    assert _states(group) == [("cold", True), ("cold", True)], group
    assert _counts(group) == (0, 2, 2), group


def test_a_group_of_cold_members_is_still_available(run):
    group = run["reaped"]["rest"]

    assert (group["is_available"], group["circuit_open"], group["state"]) == (True, False, "healthy"), group


def test_a_call_through_it_starts_a_member_and_succeeds(run):
    [outcome] = run["call"]["results"]

    assert outcome["success"] is True, outcome
    group = run["called"]["rest"]
    assert _states(group) == [("cold", True), ("ready", True)], group
    assert _counts(group) == (1, 2, 2), group


@pytest.mark.parametrize("moment", MOMENTS)
def test_every_surface_returns_the_same_group(run, moment):
    surfaces = run[moment]
    rest = surfaces["rest"]

    for surface in WHOLE:
        assert surfaces[surface] == rest, (surface, surfaces[surface], rest)


@pytest.mark.parametrize("moment", MOMENTS)
def test_the_status_tools_report_the_same_counts(run, moment):
    surfaces = run[moment]
    expected = _counts(surfaces["rest"])

    for surface in ("hangar_status", "hangar_health"):
        group = surfaces[surface]
        reported = (group["healthy_members"], group["members_in_rotation_count"], group["total_members"])
        assert reported == expected, (surface, group, expected)
    assert surfaces["hangar_status"]["state"] == surfaces["rest"]["state"]
