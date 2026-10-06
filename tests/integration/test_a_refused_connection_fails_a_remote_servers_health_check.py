"""A remote server whose upstream refuses connections fails its health checks, and its group opens its circuit (#1698).

``_refused_upstream_harness.py`` runs in a fresh interpreter: the real
``bootstrap()``, ``hangar_call`` through the served app to a real HTTP upstream,
and the health worker ``bootstrap()`` created. The upstream is then stopped and
its port closed, so every probe is refused.

The HTTP client reports a refused connection as ``ClientError``, and
``McpServer.health_check`` caught only ``OSError`` and ``TimeoutError``. The
error escaped the check, the worker logged ``background_task_failed``, and no
failure was ever recorded: the server stayed ``ready`` and the group's circuit
stayed closed for as long as the upstream was down. A paused upstream, which
times out, was counted, which is why this went unseen.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_refused_upstream_harness.py")
# As `_refused_upstream_harness.py` sets them.
FAILURES = 2


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("refused-upstream") / "run.json"
    result = subprocess.run([sys.executable, str(HARNESS), str(out)], capture_output=True, text=True, timeout=50)
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    return {**json.loads(out.read_text()), "stderr": result.stderr}


def test_the_member_served_a_call_before_its_upstream_stopped(run):
    [outcome] = run["before"]["results"]
    assert outcome["success"] is True, run["before"]
    assert run["ready"]["server_state"] == "ready" and run["ready"]["group"]["circuit_open"] is False, run["ready"]


def test_the_stopped_upstream_refuses_connections(run):
    """Not a socket left bound, whose probes would time out: that path already counted."""
    assert run["refuses"] is True


def test_each_refused_probe_is_a_failed_health_check(run):
    assert run["health_checks_failed"] == list(range(1, FAILURES + 1)), run["health_checks_failed"]


def test_the_server_is_degraded_after_the_configured_failures(run):
    after = run["after"]
    assert after["server_state"] == "degraded", after
    [member] = after["group"]["members"]
    assert member["in_rotation"] is False and member["consecutive_failures"] == FAILURES, after


def test_the_group_opens_its_circuit(run):
    group = run["after"]["group"]
    assert group["circuit_open"] is True and group["is_available"] is False, group


def test_a_refused_probe_does_not_escape_the_health_check(run):
    assert "background_task_failed" not in run["stderr"]
