"""``hangar_start(<group>)`` publishes what the group's members record (#2265).

A group starts and stops its members itself, outside the command bus. The tool
drained nothing, so each member's ``McpServerStarted`` stayed on its aggregate
until a later sweep, call or start command: in egress the members were not
projected, a withholding ``header_exposure`` block refused their tools as
``schema_unknown``, and a digest pin on them could not be checked.

``_group_start_over_mcp_harness.py`` runs in a fresh interpreter: the real
``bootstrap()`` from a file in egress, where no warm-up starts anything, then
the served tools called through the MCP server's own dispatch.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(120)

HARNESS = Path(__file__).with_name("_group_start_over_mcp_harness.py")
MOCK_TOOLS = ["add", "divide", "echo", "multiply", "power", "subtract"]
MEMBERS = ["alpha", "beta"]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    workdir = tmp_path_factory.mktemp("group-start-over-mcp")
    out = workdir / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(workdir), str(out)], capture_output=True, text=True, timeout=110
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-6000:]}"
    return dict(json.loads(out.read_text()))


def test_the_members_are_cold_and_unprojected_before_the_start(run: dict[str, Any]) -> None:
    for name in MEMBERS:
        assert run["booted"][name] == {"state": "cold", "projected": [], "left_on_the_aggregate": False}


def test_every_started_member_is_projected_when_the_tool_returns(run: dict[str, Any]) -> None:
    for name in MEMBERS:
        started = run["started"][name]
        assert started["state"] == "ready"
        assert started["projected"] == MOCK_TOOLS
        assert started["left_on_the_aggregate"] is False, "the start was left for a later drain to publish"


def test_each_start_is_published_and_the_group_is_drained(run: dict[str, Any]) -> None:
    assert run["starts_published"] == MEMBERS
    assert run["group_left_on_the_aggregate"] is False


def test_hangar_stop_publishes_each_members_stop(run: dict[str, Any]) -> None:
    assert run["stops_published"] == MEMBERS
    for name in MEMBERS:
        assert run["stopped"][name]["state"] == "cold"
        assert run["stopped"][name]["left_on_the_aggregate"] is False
