"""A member the group API adds to an ``auto_start`` group has its tools projected (#2258).

``POST /api/groups/{group_id}/members`` on a group with ``auto_start`` (the
default) starts the new member inside ``group.add_member``. The handler drained
only the group's events, so the member's ``McpServerStarted`` stayed on its
aggregate and nobody published it until a later sweep, call or start command
drained it: in egress the member's tools were not projected, a withholding
``header_exposure`` block refused them as ``schema_unknown``, and a digest pin
on them could not be checked.

``_member_added_over_rest_harness.py`` runs in a fresh interpreter: the real
``bootstrap()`` from a file in egress, where no warm-up starts anything, then
the REST API as ``serve --http`` mounts it, with no MCP call and no worker.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(120)

HARNESS = Path(__file__).with_name("_member_added_over_rest_harness.py")
MOCK_TOOLS = ["add", "divide", "echo", "multiply", "power", "subtract"]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    workdir = tmp_path_factory.mktemp("member-added-over-rest")
    out = workdir / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(workdir), str(out)], capture_output=True, text=True, timeout=110
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-6000:]}"
    return dict(json.loads(out.read_text()))


def test_the_member_is_cold_and_unprojected_before_the_add(run: dict[str, Any]) -> None:
    assert run["booted"]["solo"] == {"state": "cold", "projected": [], "left_on_the_aggregate": False}


def test_the_add_is_answered(run: dict[str, Any]) -> None:
    assert run["responses"]["create"][0] == 201, run["responses"]
    assert run["responses"]["add"] == [201, {"group_id": "pool", "mcp_server_id": "solo", "added": True}]


def test_a_member_added_over_rest_is_projected_when_the_request_returns(run: dict[str, Any]) -> None:
    added = run["added"]["solo"]
    assert added["state"] == "ready"
    assert added["projected"] == MOCK_TOOLS
    assert added["left_on_the_aggregate"] is False, "the start was left for a later drain to publish"


def test_its_start_is_published_once(run: dict[str, Any]) -> None:
    assert run["starts_published_by_the_add"] == ["solo"]


def test_a_server_nothing_added_stays_cold_and_unprojected(run: dict[str, Any]) -> None:
    assert run["added"]["spare"] == {"state": "cold", "projected": [], "left_on_the_aggregate": False}
