"""On the wiring ``serve --http`` runs, a front door is ready once its required catalogue is projected (#1446).

``_catalogue_readiness_harness.py`` runs in a fresh interpreter per mode: the
real ``bootstrap()``, ``ServerLifecycle.start()`` with its workers, its front-door
warm-up and the catalogue retry after it, real upstream processes broken on
purpose, and ``ServerLifecycle.shutdown()``. ``/health/ready`` and ``/metrics``
are asked over HTTP. The same at every default setting, long enough for the
recovery saga to give up, is
``test_readiness_falls_back_after_the_retry_window_at_default_settings.py``.

The decision it pins (maintainer, on #1446: option 3, and the follow-up after
the review of #1451):

- A front-door replica whose required backend is down at boot is not ready. It
  becomes ready without a restart once the retry projects that backend. The
  retry never starts it inside the server's own backoff.
- The retry never starts a server that is dead for ``capability_blocked`` or
  ``given_up``. When nothing else is left it ends early, as ``blocked``;
  readiness holds for the rest of the window, then falls back to today's rule.
- The retry never starts a server stopped for being idle, and a ready replica
  stays ready through an idle stop and through a later outage.
- The retry stops at shutdown.
- In ``egress`` the same configuration changes nothing.
- The readiness endpoint is unauthenticated, so it reports counts; no body it
  answers names a server or a dead reason.
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
MODES = ("recover", "blocked", "shutdown", "egress")

# As `_catalogue_readiness_harness.py` names and sets them.
IDLE, LATE = "svc-idle", "svc-late"
BLOCKED, GIVEN_UP = "svc-blocked", "svc-given-up"
DOWN = "svc-down"
LATE_BACKOFF_S = 5.0
BLOCKED_WINDOW_S = 20
#: Clock slack between the middleware's timestamps and the tracker's.
SLACK_S = 0.1


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
# A backend down at boot, every threshold at its default
# ----------------------------------------------------------------------------


def test_a_replica_whose_required_backend_is_down_at_boot_is_not_ready(recover):
    ready = recover["boot"]

    assert ready["status"] == 503, ready
    assert ready["body"]["status"] == "unhealthy"
    assert ready["body"]["catalogue"] == {
        "complete": False,
        "holds_readiness": True,
        "required": 2,
        "projected": 1,
        "missing_count": 1,
        "not_retried_count": 0,
        "retry": "running",
    }


def test_it_stays_not_ready_while_the_retry_fails(recover):
    assert recover["while_late"]["ready"]["status"] == 503
    assert recover["while_late"]["ready"]["body"]["catalogue"]["missing_count"] == 1


def test_it_becomes_ready_once_the_retry_projects_the_backend(recover):
    ready = recover["recovered"]["ready"]

    assert ready["status"] == 200, ready
    catalogue = ready["body"]["catalogue"]
    assert (catalogue["complete"], catalogue["projected"], catalogue["missing_count"]) == (True, 2, 0)
    # Read once the retry has ended, so its last attempt has been recorded (#1475).
    assert catalogue["retry"] == "finished", catalogue
    retries = recover["recovered"]["late_retries"]
    assert retries.get("failed", 0) >= 1, retries
    assert retries.get("projected") == 1.0, "the backend came back through something other than the retry"


def test_the_retry_never_starts_a_server_inside_its_backoff(recover):
    # svc-late's backoff is pinned above the retry's 2s spacing. An attempt made
    # while `can_retry()` is false would come sooner than that after a failure,
    # or be refused by the server: neither may happen.
    starts = recover["late_starts_timed"]  # [deliberate, outcome, sent_at, ended_at]
    [warm_up, *retried] = starts

    assert warm_up[:2] == [True, "failed"], starts
    assert retried and all(deliberate is False for deliberate, *_ in retried), starts
    assert all(outcome != "refused" for _, outcome, *_ in retried), f"sent inside the backoff: {starts}"
    for previous, attempt in zip(starts, retried, strict=False):
        assert previous[1] == "failed", starts
        gap = attempt[2] - previous[3]
        assert gap >= LATE_BACKOFF_S - SLACK_S, f"an attempt {gap:.2f}s after a failure, backoff {LATE_BACKOFF_S}s"


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
    assert after["ready"]["body"]["catalogue"]["complete"] is True
    assert after["ready"]["body"]["catalogue"]["retry"] == "finished"


# ----------------------------------------------------------------------------
# Servers the retry must leave alone, and the window
# ----------------------------------------------------------------------------


def test_it_is_seen_dead_for_both_reasons(blocked):
    assert blocked["dead"]["states"] == {BLOCKED: ["dead", "capability_blocked"], GIVEN_UP: ["dead", "given_up"]}


def test_the_retry_ends_early_and_readiness_holds_for_the_rest_of_the_window(blocked):
    held = blocked["held"]["ready"]

    assert held["at"] < BLOCKED_WINDOW_S, "the window ended before the check could run"
    assert held["status"] == 503
    assert held["body"]["catalogue"] == {
        "complete": False,
        "holds_readiness": True,
        "required": 2,
        "projected": 0,
        "missing_count": 2,
        "not_retried_count": 2,
        "retry": "blocked",
    }


def test_readiness_falls_back_once_the_window_ends(blocked):
    fallback = blocked["fallback"]

    assert fallback["status"] == 200, fallback
    assert fallback["at"] >= BLOCKED_WINDOW_S
    assert fallback["body"]["status"] == "healthy"
    catalogue = fallback["body"]["catalogue"]
    assert (catalogue["holds_readiness"], catalogue["complete"], catalogue["retry"]) == (False, False, "blocked")


def test_a_capability_blocked_or_given_up_server_is_never_started_by_the_retry(blocked):
    assert blocked["held"]["states"] == {BLOCKED: ["dead", "capability_blocked"], GIVEN_UP: ["dead", "given_up"]}
    assert blocked["retries"] == {BLOCKED: {}, GIVEN_UP: {}}
    assert blocked["blocked_launches"] == 1, "the blocked upstream was launched again"
    assert blocked["given_up_starts"] == 0
    assert blocked["given_up_ever_served"] is False, "revived after its upstream came back"


# ----------------------------------------------------------------------------
# Shutdown
# ----------------------------------------------------------------------------


def test_the_retry_stops_at_shutdown(runs):
    shutdown = runs["shutdown"]

    assert shutdown["before"]["attempts"] >= 2
    assert shutdown["before"]["ready"]["body"]["catalogue"]["retry"] == "running"
    assert shutdown["after"]["attempts_since"] == 0, "a start after shutdown"
    assert shutdown["after"]["ready"]["body"]["catalogue"]["retry"] == "stopped"


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


# ----------------------------------------------------------------------------
# What an unauthenticated caller can read
# ----------------------------------------------------------------------------


def test_no_readiness_body_names_a_server_or_a_dead_reason(runs):
    # `/health/ready` is on the auth skip-list, so it carries counts; the ids are logged.
    bodies = [
        runs["recover"]["boot"],
        runs["recover"]["while_late"]["ready"],
        runs["recover"]["recovered"]["ready"],
        runs["recover"]["after_outage"]["ready"],
        runs["blocked"]["held"]["ready"],
        runs["blocked"]["fallback"],
        runs["shutdown"]["before"]["ready"],
        runs["shutdown"]["after"]["ready"],
        runs["egress"]["boot"],
        runs["egress"]["later"],
    ]
    text = json.dumps([ready["body"] for ready in bodies])

    for word in (IDLE, LATE, BLOCKED, GIVEN_UP, DOWN, "capability_blocked", "given_up", "start_failed", "crashed"):
        assert word not in text
