"""A reload restarts only the servers whose settings changed, on the app ``serve --http`` serves (#1426).

Every reload restarted every server whose file left ``resources`` out, which is
most of them: the reload diff compared the running server's default resources
with the file's absent ones, counted it as changed, and stopped it.

``_reload_keeps_harness.py`` runs in a fresh interpreter: the real
``bootstrap()`` from a file, real stdio subprocess servers started by
``hangar_call`` over streamable HTTP, and reloads through
``POST /api/config/reload``. What each server's process did is read off its pid
and its ``Popen``. Unit-level coverage, including docker and remote servers and
every server setting, is in ``tests/unit/test_a_reload_applies_the_whole_configuration.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

# One gateway, booted and reloaded twice, with five real upstreams.
pytestmark = pytest.mark.timeout(120)

HARNESS = Path(__file__).with_name("_reload_keeps_harness.py")


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    workdir = tmp_path_factory.mktemp("reload-keeps")
    out = workdir / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(workdir), str(out)],
        capture_output=True,
        text=True,
        timeout=110,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-6000:]}"
    return dict(json.loads(out.read_text()))


def test_every_server_was_running_before_the_reload(run: dict[str, Any]) -> None:
    # Otherwise a surviving pid below would say nothing.
    assert run["boot"]["calls"] == {"keep": "served", "edit": "served", "drop": "served", "m1": "served"}
    assert all(run["boot"]["pids"][sid] for sid in ("keep", "edit", "drop", "m1"))


def test_the_reload_reports_only_the_edited_server_as_updated(run: dict[str, Any]) -> None:
    assert run["edited"]["status"] == 200
    assert run["edited"]["diff"] == {
        "mcp_servers_added": ["late"],
        "mcp_servers_removed": ["drop"],
        "mcp_servers_updated": ["edit"],
        "mcp_servers_unchanged": ["keep", "m1"],
    }


@pytest.mark.parametrize("mcp_server_id", ["keep", "m1"])
def test_an_unchanged_server_keeps_its_process(run: dict[str, Any], mcp_server_id: str) -> None:
    edited = run["edited"]

    assert edited["same_object"][mcp_server_id] is True
    assert edited["exited"][mcp_server_id] is False
    assert edited["pids"][mcp_server_id] == run["boot"]["pids"][mcp_server_id]


def test_a_policy_change_on_an_unchanged_server_takes_effect_without_a_restart(run: dict[str, Any]) -> None:
    boot_pid = run["boot"]["pids"]["keep"]

    assert run["edited"]["calls"]["keep"] == "ToolAccessDeniedError"
    assert run["edited"]["pids"]["keep"] == boot_pid

    lifted = run["lifted"]
    assert lifted["diff"]["mcp_servers_updated"] == []
    assert lifted["calls"]["keep"] == "served"
    assert lifted["pids"]["keep"] == boot_pid


def test_a_changed_server_restarts_with_the_files_settings(run: dict[str, Any]) -> None:
    edited = run["edited"]

    assert edited["same_object"]["edit"] is False
    assert edited["exited"]["edit"] is True
    assert edited["calls"]["edit"] == "served"
    assert edited["pids"]["edit"] not in (None, run["boot"]["pids"]["edit"])
    # The mock names its `add` tool after this variable: the new process runs with the new env.
    assert edited["edit_description"] == "after"


def test_a_removed_server_stops_and_is_gone(run: dict[str, Any]) -> None:
    edited = run["edited"]

    assert edited["exited"]["drop"] is True
    assert edited["drop_registered"] is False
    # Refused before it runs: the batch names a server that no longer exists.
    assert edited["calls"]["drop"] == "invalid: McpServer 'drop' not found"


def test_an_added_server_registers_and_serves(run: dict[str, Any]) -> None:
    assert run["edited"]["calls"]["late"] == "served"
    assert run["edited"]["pids"]["late"]


def test_the_group_follows_the_file_and_holds_the_kept_member(run: dict[str, Any]) -> None:
    assert run["edited"]["m1_weight"] == 2
    assert run["edited"]["m1_member_is_the_running_server"] is True
