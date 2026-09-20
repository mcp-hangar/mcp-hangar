"""A server a reload adds has a log buffer, and its output reaches the same API (#1502).

Per-server log buffers were attached once, at boot. A reload never attached one,
so a server it ADDED had none at all: the stderr reader was never started for
it, and ``GET /api/mcp_servers/{id}/logs`` served an empty list for a running
server. A server a reload REMOVED kept its buffer registered under its id for
the life of the process.

``_added_server_logs_harness.py`` runs in a fresh interpreter: the real
``bootstrap()`` from a file, real stdio subprocess servers each writing a line
of its own to stderr, the reload over REST, the calls through ``hangar_call``
over streamable HTTP, and the logs read back through the API. The unit-level
versions, through the same ``ReloadConfigurationHandler``, are in
``tests/unit/test_a_reload_applies_the_whole_configuration.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# One gateway, booted and reloaded once, with four real upstreams.
pytestmark = pytest.mark.timeout(180)

HARNESS = Path(__file__).with_name("_added_server_logs_harness.py")


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    workdir = tmp_path_factory.mktemp("added-server-logs")
    out = workdir / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(workdir), str(out)],
        capture_output=True,
        text=True,
        timeout=170,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-6000:]}"
    return dict(json.loads(out.read_text()))


def test_a_booted_servers_output_reaches_its_log(run: dict[str, Any]) -> None:
    # Otherwise nothing below says anything: this is the log every other
    # assertion here compares an added, rebuilt or removed server against.
    boot = run["boot"]

    assert boot["logs"]["keep"] == ["keep is up"]
    assert boot["logs"]["edit"] == ["edit is up before"]
    assert boot["logs"]["drop"] == ["drop is up"]
    assert boot["wired"] == {"keep": True, "edit": True, "drop": True}


def test_the_reload_adds_removes_and_rebuilds_what_the_file_says(run: dict[str, Any]) -> None:
    edited = run["edited"]

    assert edited["status"] == 200
    assert edited["diff"]["mcp_servers_added"] == ["late"]
    assert edited["diff"]["mcp_servers_removed"] == ["drop"]
    assert edited["diff"]["mcp_servers_updated"] == ["edit"]
    assert edited["diff"]["mcp_servers_unchanged"] == ["keep"]


def test_the_added_servers_output_reaches_the_same_log(run: dict[str, Any]) -> None:
    edited = run["edited"]

    assert edited["logs"]["late"] == ["late is up"], "a server a reload added had no buffer to write to"
    assert edited["wired"]["late"], "the server must fill the very buffer the logs API reads"


def test_the_rebuilt_server_keeps_the_log_it_had(run: dict[str, Any]) -> None:
    """#1498's behaviour, through the same reload: the carried buffer keeps its lines."""
    edited = run["edited"]

    assert edited["logs"]["edit"] == ["edit is up before", "edit is up after"]
    assert edited["same_buffer_as_boot"]["edit"]
    assert edited["wired"]["edit"]


def test_the_kept_server_keeps_the_log_it_had(run: dict[str, Any]) -> None:
    edited = run["edited"]

    assert edited["logs"]["keep"] == ["keep is up"], "it was never stopped, and nothing replaced its buffer"
    assert edited["same_buffer_as_boot"]["keep"]
    assert edited["wired"]["keep"]


def test_the_removed_servers_log_does_not_outlive_it(run: dict[str, Any]) -> None:
    edited = run["edited"]

    assert not edited["drop_in_repository"]
    assert not edited["drop_registered"], "its output stayed registered under an id now free"
    # The server is gone, so the API has nothing to serve a log for.
    assert edited["logs"]["drop"] == 404
