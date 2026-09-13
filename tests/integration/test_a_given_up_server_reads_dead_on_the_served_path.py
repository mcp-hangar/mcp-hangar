"""On the wiring ``serve --http`` runs, a given-up server reads dead and keeps its last-healthy time (#1361, #1359).

``_dead_server_harness.py`` runs once, in a fresh interpreter: the real
``bootstrap()``, the served MCP app, the health and GC workers ``bootstrap()``
created and the recovery saga it registered. Two servers run a real upstream
process that is made to fail ``tools/list``. A health check fails and degrades
each one; the saga restarts it three times, each start fails, and the saga
gives up. Then the upstreams come back: one server is revived by
``hangar_start``, the other by ``hangar_call``.

What this pins, and what a unit test with a mock bus cannot:

- The give-up reaches ``mcp_hangar_mcp_server_state`` as 4 on the served path.
  Before, it read 0: the saga's stop published ``McpServerStopped``, which the
  metrics handler maps to ``cold``. The order matters too: the saga hears the
  degrade after the metrics handler has set 3, and its nested give-up must be
  what the gauge ends on.
- The saga's own retries ran. The give-up is not a short cut around them.
- ``mcp_hangar_mcp_server_last_healthy_timestamp_seconds`` is written on the
  served path, appears in the ``/metrics`` body, and keeps the last passing
  check's time through the give-up and through a stop. The live tier scrapes
  the same series over HTTP (``tests/live/test_t0_last_healthy.py``).
- Nothing revives a dead server on its own. The workers run for three seconds
  with both dead: no state change, no health check, no restart.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_dead_server_harness.py")

# As `_dead_server_harness.py` names them.
BY_START, BY_CALL = "svc-a", "svc-b"
SERVERS = (BY_START, BY_CALL)
#: One failing health check degrades the server, then three restarts fail.
DEGRADES_BEFORE_GIVING_UP = 4


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("dead-server") / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(out)],
        capture_output=True,
        text=True,
        timeout=55,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    return json.loads(out.read_text())


def _events_until_dead(run: dict[str, Any], server: str) -> list[list[Any]]:
    """The server's events up to the moment both were seen dead, before anything revived them."""
    return [event[1:] for event in run["events"][: run["dead_mark"]] if event[0] == server]


def _outcome(batch: dict[str, Any]) -> dict[str, Any]:
    [result] = batch["results"]
    return result


@pytest.mark.parametrize("server", SERVERS)
def test_both_serve_and_are_seen_working(run, server):
    assert _outcome(run["first_calls"][server])["success"] is True, run["first_calls"][server]
    healthy = run["healthy"][server]
    assert (healthy["domain_state"], healthy["state"], healthy["up"]) == ("ready", 2.0, 1.0)
    assert healthy["last_healthy"] is not None


@pytest.mark.parametrize("server", SERVERS)
def test_the_saga_retries_then_gives_up(run, server):
    events = _events_until_dead(run, server)
    degraded_at = [i for i, e in enumerate(events) if e[0] == "degraded"]
    [dead_at] = [i for i, e in enumerate(events) if e == ["state", "dead"]]
    restarts = [i for i, e in enumerate(events) if e == ["state", "initializing"]][1:]  # after the first start

    assert len(degraded_at) == DEGRADES_BEFORE_GIVING_UP, events
    # Every restart comes before the give-up. The give-up itself is delivered
    # inside the last degrade's publish -- the saga answers that event with a
    # nested one -- so this watcher, subscribed after the saga, hears `dead`
    # just before it hears that degrade.
    assert len(restarts) == DEGRADES_BEFORE_GIVING_UP - 1, events
    assert max(restarts) < dead_at, "a restart after the give-up: the saga revived what it gave up on"
    assert run["dead"][server]["stops"] == {"max_retries_exceeded": 1.0, "shutdown": 0.0}


@pytest.mark.parametrize("server", SERVERS)
def test_a_given_up_server_reads_dead(run, server):
    dead = run["dead"][server]

    assert (dead["domain_state"], dead["state"], dead["up"]) == ("dead", 4.0, 0.0)


@pytest.mark.parametrize("server", SERVERS)
def test_its_last_healthy_time_survives_the_give_up(run, server):
    # Read once no check could pass any more; it is the last one that did.
    last = run["broken"][server]["last_healthy"]

    assert last is not None and last >= run["healthy"][server]["last_healthy"]
    assert run["dead"][server]["last_healthy"] == last
    assert run["while_dead"]["snapshot"][server]["last_healthy"] == last


@pytest.mark.parametrize("server", SERVERS)
def test_nothing_revives_it_on_its_own(run, server):
    during, before = run["while_dead"]["snapshot"][server], run["dead"][server]

    assert run["while_dead"]["events"][server] == [], "a state change, health check or restart while dead"
    assert (during["domain_state"], during["state"]) == ("dead", 4.0)
    assert during["health_checks"] == before["health_checks"], "a dead server was health-checked"


def test_an_explicit_start_revives_it(run):
    after = run["after_revival"][BY_START]

    assert run["revived"][BY_START]["state"] == "ready", run["revived"][BY_START]
    assert (after["domain_state"], after["state"]) == ("ready", 2.0)
    assert after["last_healthy"] > run["broken"][BY_START]["last_healthy"]


def test_a_call_revives_it(run):
    after = run["after_revival"][BY_CALL]

    assert _outcome(run["revived"][BY_CALL])["success"] is True, run["revived"][BY_CALL]
    assert (after["domain_state"], after["state"]) == ("ready", 2.0)
    assert after["last_healthy"] > run["broken"][BY_CALL]["last_healthy"]


def test_its_last_healthy_time_survives_a_stop(run):
    stopped, later = run["stopped"], run["still_cold"]

    assert (stopped["domain_state"], stopped["state"]) == ("cold", 0.0)
    assert stopped["last_healthy"] is not None
    assert stopped["last_healthy"] >= run["after_revival"][BY_START]["last_healthy"]
    assert later["last_healthy"] == stopped["last_healthy"], "moved while cold: nothing probes a cold server"
