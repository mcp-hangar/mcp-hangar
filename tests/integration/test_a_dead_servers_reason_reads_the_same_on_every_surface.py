"""On the app ``serve --http`` serves, a dead server's reason reads the same on every surface (#1418).

``_dead_reason_harness.py`` runs the real ``bootstrap()`` in a fresh interpreter,
with real upstream processes, and serves the MCP app and the REST router as
``ServerLifecycle.run_http`` does. It drives one server dead for each reason
down the path production takes: the health worker finds a crash and a broken
upstream, the recovery saga gives up, and ``hangar_start`` fails for a start
failure and for a capability block. Then it reads ``GET /api/mcp_servers/{id}``,
``GET /api/mcp_servers``, ``hangar_details``, ``hangar_list`` and
``hangar_status``.

What this pins, and what the unit tests with a stand-in transport cannot:

- The REST and MCP answers agree, reason and times included, over the served
  transports.
- A server that is not dead reads ``dead: null`` on every surface.
- The upstreams' own text, which reached Hangar through a start failure's
  output and a failing ``tools/list``, is on no surface that reports the reason.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_dead_reason_harness.py")

# As `_dead_reason_harness.py` names them.
SENTINEL = "SENTINEL-1418-what-the-upstream-said"
REASONS = {
    "gave-up": "given_up",
    "crashed": "crashed",
    "start-failed": "start_failed",
    "blocked": "capability_blocked",
}
HEALTHY = "healthy"
SURFACES = (
    "GET /api/mcp_servers/{id}",
    "GET /api/mcp_servers",
    "hangar_details",
    "hangar_list",
    "hangar_status",
)


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("dead-reason") / "run.json"
    result = subprocess.run([sys.executable, str(HARNESS), str(out)], capture_output=True, text=True, timeout=55)
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    report = json.loads(out.read_text())
    assert report["all_dead"], report["domain"]
    return report


@pytest.mark.parametrize("server", sorted(REASONS))
def test_every_surface_gives_the_same_reason(run: dict[str, Any], server: str) -> None:
    surfaces = run["surfaces"][server]
    dead = surfaces["hangar_details"]["dead"]

    assert tuple(surfaces) == SURFACES
    assert {surface: entry["dead"] for surface, entry in surfaces.items()} == dict.fromkeys(SURFACES, dead)
    assert {surface: entry["state"] for surface, entry in surfaces.items()} == dict.fromkeys(SURFACES, "dead")
    assert dead["reason"] == REASONS[server] == run["domain"][server][1]
    assert dead["since"] is not None
    assert f"Failed ({REASONS[server]}): " in surfaces["hangar_status"]["note"]


@pytest.mark.parametrize("server", sorted(REASONS))
def test_what_starts_it_again_follows_the_reason(run: dict[str, Any], server: str) -> None:
    dead = run["surfaces"][server]["hangar_details"]["dead"]

    if REASONS[server] == "capability_blocked":
        assert (dead["revived_by"], dead["retry_allowed_at"]) == ("start", None)
    else:
        assert dead["revived_by"] == "call_or_start"


def test_a_server_that_is_not_dead_reads_null_on_every_surface(run: dict[str, Any]) -> None:
    surfaces = run["surfaces"][HEALTHY]

    assert {surface: entry["dead"] for surface, entry in surfaces.items()} == dict.fromkeys(SURFACES)
    assert surfaces["hangar_details"]["state"] == "ready"


def test_no_surface_repeats_what_the_upstreams_said(run: dict[str, Any]) -> None:
    assert SENTINEL in json.dumps(run["upstream_logs"]), "the upstream's text reached Hangar"
    assert run["bodies"]
    assert not [body for body in run["bodies"] if SENTINEL in body]
