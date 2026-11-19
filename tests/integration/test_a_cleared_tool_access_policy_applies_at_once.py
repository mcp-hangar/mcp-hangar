"""A tool access policy cleared over REST applies at once, as a restart would apply it (#2178).

Clearing a policy deleted the stored row and left the resolver alone, so the
cleared policy held until the next restart. Now the clear takes it back: the
scope holds the file's policy when the file declares one, as a restart adopts
the file and then replays the rows that remain, and no policy when it does not.

``_policy_clear_harness.py`` runs in a fresh interpreter: the real
``bootstrap()`` from a file, real stdio subprocess servers, REST and MCP calls
under an API key. A second interpreter boots on the first one's data.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(180)

HARNESS = Path(__file__).with_name("_policy_clear_harness.py")
DENIED = "ToolAccessDeniedError"
FILE_POLICY = {"declared": {"add": DENIED, "echo": "served"}, "plain": {"add": "served", "echo": "served"}}


def _step(workdir: Path, step: str) -> dict[str, Any]:
    out = workdir / f"{step}.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(workdir), str(out), step],
        capture_output=True,
        text=True,
        timeout=80,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-6000:]}"
    return dict(json.loads(out.read_text()))


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    workdir = tmp_path_factory.mktemp("policy-clear")
    return {**_step(workdir, "change"), **_step(workdir, "restart")}


def test_the_file_policy_holds_before_any_change(run: dict[str, Any]) -> None:
    assert run["before"] == FILE_POLICY


def test_a_policy_set_over_rest_replaces_the_files_at_once(run: dict[str, Any]) -> None:
    only_echo_denied = {"add": "served", "echo": DENIED}
    assert run["after_set"] == {"declared": only_echo_denied, "plain": only_echo_denied}


def test_a_clear_falls_back_to_the_files_policy_or_to_none_without_a_restart(run: dict[str, Any]) -> None:
    assert run["after_clear"] == FILE_POLICY
    assert run["after_second_clear"] == FILE_POLICY


def test_the_state_after_a_clear_is_the_state_after_a_restart(run: dict[str, Any]) -> None:
    assert run["after_restart"] == run["after_clear"]
