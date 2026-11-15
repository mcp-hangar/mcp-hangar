"""A group member started while the configuration loads has its tools projected (#2257).

A group with ``auto_start`` (the default) starts its members while the file is
loaded, before the projection handler subscribes to ``McpServerStarted``. The
start was recorded on the member and published by nobody, so the registry held
nothing for it: its tools were missing from the flat listing, a pin on it could
not be checked, and a withholding ``header_exposure`` block refused every one of
its tools as ``schema_unknown``.

``_members_started_at_load_harness.py`` runs in a fresh interpreter: the real
``bootstrap()`` from a file, then a reload, in egress, where no warm-up starts
anything, with no call and no worker running.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(120)

HARNESS = Path(__file__).with_name("_members_started_at_load_harness.py")
MOCK_TOOLS = ["add", "divide", "echo", "multiply", "power", "subtract"]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    workdir = tmp_path_factory.mktemp("members-started-at-load")
    out = workdir / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(workdir), str(out)], capture_output=True, text=True, timeout=110
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-6000:]}"
    return dict(json.loads(out.read_text()))


@pytest.mark.parametrize("member", ["inline", "named"])
def test_a_member_started_at_boot_is_projected(run: dict[str, Any], member: str) -> None:
    booted = run["booted"][member]
    assert booted["state"] == "ready"
    assert booted["projected"] == MOCK_TOOLS
    assert booted["left_on_the_aggregate"] is False, "the start is published once, at boot, not again later"


def test_a_server_in_no_group_is_not_started_or_projected_at_boot(run: dict[str, Any]) -> None:
    # Not started at load, so there is no start to publish: its first call starts and projects it.
    assert run["booted"]["solo"] == {"state": "cold", "projected": [], "left_on_the_aggregate": False}


def test_a_member_a_reload_adds_and_starts_is_projected(run: dict[str, Any]) -> None:
    late = run["reloaded"]["late-inline"]
    assert late["state"] == "ready"
    assert late["projected"] == MOCK_TOOLS
    assert late["left_on_the_aggregate"] is False


def test_the_reload_publishes_each_start_once_and_only_the_new_one(run: dict[str, Any]) -> None:
    # The kept members were started at boot and their starts published then.
    assert run["starts_published_by_the_reload"] == ["late-inline"]
    for member in ("inline", "named"):
        assert run["reloaded"][member]["projected"] == MOCK_TOOLS
