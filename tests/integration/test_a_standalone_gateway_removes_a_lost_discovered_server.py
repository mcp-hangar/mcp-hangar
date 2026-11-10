"""A standalone gateway on SQLite removes a discovered server its source no longer reports.

Storage no peer shares runs no lease keeper, so the gateway holds no lease. The
fleet writer still fenced discovery's deletion on one, refused it, and the
server stayed in the fleet and in its row, and came back on every restart.

``_standalone_discovery_harness.py`` runs each phase in a process of its own on
one data directory: the real ``bootstrap()`` from a file, and the discovery
orchestrator it builds.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(120)

HARNESS = Path(__file__).with_name("_standalone_discovery_harness.py")
SERVER = "found-one"


def _phases(workdir: Path, storage: str) -> dict[str, Any]:
    env = dict(os.environ)
    env.pop("MCP_PERSISTENCE_ENABLED", None)
    if storage == "legacy":
        env["MCP_PERSISTENCE_ENABLED"] = "true"
        env["MCP_DATABASE_PATH"] = "data/mcp_hangar.db"
    outcomes: dict[str, Any] = {}
    for phase in ("first", "restart"):
        out = workdir / f"{phase}.json"
        result = subprocess.run(
            [sys.executable, str(HARNESS), phase, storage, str(workdir), str(out)],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        assert result.returncode == 0 and out.exists(), f"{phase} exited {result.returncode}:\n{result.stderr[-6000:]}"
        outcomes[phase] = json.loads(out.read_text())
        outcomes[phase]["log"] = result.stdout + result.stderr
    return outcomes


@pytest.fixture(scope="module", params=["sqlite", "legacy"])
def run(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _phases(tmp_path_factory.mktemp(f"standalone-{request.param}"), request.param)


def test_the_gateway_is_standalone_and_registered_the_server(run: dict[str, Any]) -> None:
    first = run["first"]
    assert first["keeper"] is False
    assert first["registered"] == [SERVER]
    assert first["rows_registered"] == [SERVER]


def test_the_lost_server_is_gone_from_the_fleet(run: dict[str, Any]) -> None:
    first = run["first"]
    assert first["expired"] == [SERVER]
    assert first["after_loss"] == []


def test_the_lost_server_is_gone_from_its_row(run: dict[str, Any]) -> None:
    assert run["first"]["rows"] == []


def test_the_deletion_was_not_refused_as_fenced(run: dict[str, Any]) -> None:
    assert "discovery_deregistration_fenced" not in run["first"]["log"]


def test_a_restart_does_not_bring_it_back(run: dict[str, Any]) -> None:
    restart = run["restart"]
    assert restart["at_boot"] == []
    assert restart["rows"] == []
