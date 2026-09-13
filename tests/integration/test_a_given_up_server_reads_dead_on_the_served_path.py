"""On the wiring ``serve --http`` runs, a given-up server reads dead and keeps its last-healthy time (#1361, #1359).

``_dead_server_harness.py`` runs in a fresh interpreter per mode: the real
``bootstrap()``, the served MCP app, the health and GC workers ``bootstrap()``
created and the sagas it registered, with real upstream processes that are
broken on purpose.

``single``: a health check fails and degrades a server, the saga restarts it,
the start fails, and the saga gives up. A call inside the server's backoff is
refused; ``hangar_start`` revives one server and a call after the backoff the
other.

``group``: a one-member group. The member's process is killed between two
requests -- a crash -- and the next call through the group restarts it, as it
always has. Then its upstream breaks until the saga gives up: the member leaves
rotation, a call through the group is refused, and ``hangar_start`` brings it
back.

What this pins, and what a unit test with a mock bus cannot:

- The give-up reaches ``mcp_hangar_mcp_server_state`` as 4 on the served path.
  The saga hears the degrade after the metrics handler has set 3, and its
  nested give-up must be what the gauge ends on.
- The saga's own retry ran. The give-up is not a short cut around it.
- A call to a dead server respects the server's backoff: before it, the call
  is refused and nothing starts.
- ``mcp_hangar_mcp_server_last_healthy_timestamp_seconds`` is written on the
  served path, appears in the ``/metrics`` body, and keeps the last passing
  check's time through the give-up and through a stop. The live tier scrapes
  the same series over HTTP (``tests/live/test_t0_last_healthy.py``).
- Nothing revives a dead server on its own: the workers run for three seconds
  with it dead and nothing happens to it.
- A group restarts a member that crashed, and does not route to one Hangar
  gave up on.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_dead_server_harness.py")
MODES = ("single", "group")

# As `_dead_server_harness.py` names them.
BY_START, BY_CALL = "svc-a", "svc-b"
SERVERS = (BY_START, BY_CALL)
MEMBER = "member-a"
#: One failing health check degrades the server, then the one restart fails.
DEGRADES_BEFORE_GIVING_UP = 2


def _run(mode: str, tmp: Path) -> dict[str, Any]:
    out = tmp / mode / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(out)],
        capture_output=True,
        text=True,
        timeout=55,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{mode}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return json.loads(out.read_text())


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    # Concurrently, under the 60s pytest-timeout the integration job applies.
    tmp = tmp_path_factory.mktemp("dead-server")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


@pytest.fixture
def run(runs):
    return runs["single"]


@pytest.fixture
def grouped(runs):
    return runs["group"]


def _events_until_dead(run: dict[str, Any], server: str) -> list[list[Any]]:
    """The server's events up to the moment both were seen dead, before anything revived them."""
    return [event[1:] for event in run["events"][: run["dead_mark"]] if event[0] == server]


def _outcome(batch: dict[str, Any]) -> dict[str, Any]:
    [result] = batch["results"]
    return result


def _member(status: dict[str, Any]) -> dict[str, Any]:
    [member] = status["members"]
    return member


# ----------------------------------------------------------------------------
# A server on its own
# ----------------------------------------------------------------------------


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
    [dead_at] = [i for i, e in enumerate(events) if e == ["state", "dead", "given_up"]]
    restarts = [i for i, e in enumerate(events) if e[:2] == ["state", "initializing"]][1:]  # after the first start

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


def test_a_call_inside_its_backoff_is_refused(run):
    refused = _outcome(run["call_in_backoff"])

    assert (refused["success"], refused["error_type"]) == (False, "CircuitBreakerOpen"), refused
    assert "retry in" in refused["error"]


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


def test_a_call_after_its_backoff_revives_it(run):
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


# ----------------------------------------------------------------------------
# A group member: crashed, then given up on
# ----------------------------------------------------------------------------


def test_the_group_serves_before_anything_goes_wrong(grouped):
    assert _outcome(grouped["first_call"])["success"] is True, grouped["first_call"]
    assert grouped["healthy"]["healthy_count"] == 1


def test_a_crashed_member_stays_in_rotation_but_is_not_counted_healthy(grouped):
    status = grouped["crashed"]

    assert _member(status)["state"] == "dead"
    assert _member(status)["in_rotation"] is True
    assert status["healthy_count"] == 0, "a dead member counted as healthy"
    assert ["state", "dead", "crashed"] in [e[1:] for e in grouped["events"] if e[0] == MEMBER]


def test_a_call_through_the_group_restarts_a_crashed_member(grouped):
    # As it did before `dead` was visible: the group selected the member and
    # the call started it. Refusing it would leave a one-member group down
    # until an operator acted.
    assert _outcome(grouped["crash_call"])["success"] is True, grouped["crash_call"]
    after = grouped["after_crash_call"]
    assert (_member(after)["state"], after["healthy_count"]) == ("ready", 1)


def test_a_given_up_member_leaves_rotation(grouped):
    status = grouped["given_up"]

    assert (_member(status)["state"], _member(status)["in_rotation"]) == ("dead", False)
    assert status["healthy_count"] == 0
    assert ["state", "dead", "given_up"] in [e[1:] for e in grouped["events"] if e[0] == MEMBER]


def test_a_call_through_the_group_does_not_revive_a_given_up_member(grouped):
    refused = _outcome(grouped["given_up_call"])

    assert (refused["success"], refused["error_type"]) == (False, "NoAvailableMemberError"), refused


def test_an_explicit_start_brings_a_given_up_member_back(grouped):
    assert grouped["start"]["state"] == "ready", grouped["start"]
    after = grouped["after_start"]
    assert (_member(after)["in_rotation"], after["healthy_count"]) == (True, 1)
    assert _outcome(grouped["start_call"])["success"] is True, grouped["start_call"]
