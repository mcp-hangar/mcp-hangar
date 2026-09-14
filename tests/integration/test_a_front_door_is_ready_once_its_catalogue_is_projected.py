"""On the wiring ``serve --http`` runs, a front door is ready once its required catalogue is projected (#1446).

``_catalogue_readiness_harness.py`` runs in a fresh interpreter per mode: the
real ``bootstrap()``, ``ServerLifecycle.start()`` with its workers, its front-door
warm-up and the catalogue retry after it, real upstream processes broken on
purpose, and ``ServerLifecycle.shutdown()``. ``/health/ready`` and ``/metrics``
are asked over HTTP.

The decision it pins (maintainer, on #1446: option 3):

- A front-door replica whose required backend is down at boot is not ready, and
  says what is missing. It becomes ready without a restart once the retry
  projects that backend.
- The retry never starts a server that is dead for ``capability_blocked`` or
  ``given_up``. The replica stays not ready and names the reason. That is the
  intended answer: it cannot serve the catalogue it was told to, and bringing
  either back is an operator's deliberate start.
- The retry never starts a server stopped for being idle, and a ready replica
  stays ready through an idle stop and through a later outage.
- The retry stops at shutdown.
- In ``egress`` the same configuration changes nothing.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_catalogue_readiness_harness.py")
MODES = ("recover", "blocked", "egress")

# As `_catalogue_readiness_harness.py` names them.
IDLE, LATE = "svc-idle", "svc-late"
BLOCKED, GIVEN_UP, DOWN = "svc-blocked", "svc-given-up", "svc-down"


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
    tmp = tmp_path_factory.mktemp("catalogue-readiness")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


@pytest.fixture
def recover(runs):
    return runs["recover"]


@pytest.fixture
def blocked(runs):
    return runs["blocked"]


@pytest.fixture
def egress(runs):
    return runs["egress"]


# ----------------------------------------------------------------------------
# A backend down at boot
# ----------------------------------------------------------------------------


def test_a_replica_whose_required_backend_is_down_at_boot_is_not_ready(recover):
    ready = recover["boot"]

    assert ready["status"] == 503, ready
    assert ready["body"]["status"] == "unhealthy"
    assert ready["body"]["catalogue"] == {"status": "waiting", "missing": [LATE], "not_retried": {}, "retry": "running"}


def test_it_stays_not_ready_while_the_retry_fails(recover):
    assert recover["while_late"]["ready"]["status"] == 503
    assert recover["while_late"]["ready"]["body"]["catalogue"]["missing"] == [LATE]


def test_it_becomes_ready_once_the_retry_projects_the_backend(recover):
    ready = recover["recovered"]["ready"]

    assert ready["status"] == 200, ready
    assert ready["body"]["catalogue"] == {"status": "complete"}
    retries = recover["recovered"]["late_retries"]
    assert retries.get("failed", 0) >= 1, retries
    assert retries.get("projected") == 1.0, "the backend came back through something other than the retry"


def test_an_idle_stopped_server_is_not_started_again(recover):
    # svc-idle was projected at boot and stopped for being idle while the
    # retry was still working on svc-late. #1429's reconciler restarted it.
    assert recover["while_late"]["idle_state"] == ["cold", None]
    assert recover["while_late"]["idle_stops"] >= 1
    assert recover["idle_starts"] == 1, "started again after its idle stop"
    assert recover["idle_retries"] == {}
    assert recover["after_outage"]["idle_state"] == ["cold", None]


def test_a_ready_replica_stays_ready_through_an_idle_stop_and_a_later_outage(recover):
    after = recover["after_outage"]

    assert after["late_state"][0] != "ready", after
    assert after["ready"]["status"] == 200, after
    assert after["ready"]["body"]["catalogue"] == {"status": "complete"}


# ----------------------------------------------------------------------------
# Servers the retry must leave alone, and shutdown
# ----------------------------------------------------------------------------


def test_it_is_seen_dead_for_both_reasons(blocked):
    assert blocked["dead"] == {BLOCKED: ["dead", "capability_blocked"], GIVEN_UP: ["dead", "given_up"]}


def test_a_capability_blocked_or_given_up_server_is_never_started_by_the_retry(blocked):
    window = blocked["window"]

    assert window["states"][BLOCKED] == ["dead", "capability_blocked"]
    assert window["states"][GIVEN_UP] == ["dead", "given_up"], "revived after its upstream came back"
    assert blocked["retries"] == {BLOCKED: {}, GIVEN_UP: {}}
    assert blocked["blocked_launches"] == 1, "the blocked upstream was launched again"
    assert blocked["given_up_starts"] == 0
    assert blocked["given_up_ever_served"] is False


def test_the_retry_was_running_all_along(blocked):
    # Not a trivial pass: in the same window it kept trying the one server it may start.
    assert blocked["window"]["down_attempts"] >= 1
    assert blocked["window"]["ready"]["body"]["catalogue"]["retry"] == "running"


def test_readiness_stays_503_and_says_why(blocked):
    ready = blocked["window"]["ready"]

    assert ready["status"] == 503
    assert ready["body"]["catalogue"]["missing"] == [BLOCKED, GIVEN_UP, DOWN]
    assert ready["body"]["catalogue"]["not_retried"] == {BLOCKED: "capability_blocked", GIVEN_UP: "given_up"}


def test_the_retry_stops_at_shutdown(blocked):
    after = blocked["after_shutdown"]

    assert after["down_attempts"] == 0, "a start after shutdown"
    assert after["ready"]["body"]["catalogue"]["retry"] == "stopped"


# ----------------------------------------------------------------------------
# Egress
# ----------------------------------------------------------------------------


def test_egress_readiness_is_unchanged(egress):
    for ready in (egress["boot"], egress["later"]):
        assert ready["status"] == 200, ready
        assert "catalogue" not in ready["body"]


def test_egress_starts_nothing(egress):
    assert egress["states"] == {IDLE: ["cold", None], LATE: ["cold", None]}
    assert egress["starts"] == {IDLE: 0, LATE: 0}
    assert egress["retries"] == {IDLE: {}, LATE: {}}
    assert egress["ever_served"] == {IDLE: False, LATE: False}
