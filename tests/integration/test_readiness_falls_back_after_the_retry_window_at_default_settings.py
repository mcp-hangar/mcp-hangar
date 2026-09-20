"""At default settings, a required backend that stays down cannot hold readiness forever (#1446).

The review of #1451 found it, measured: a required server down at boot fails
three starts and goes ``degraded`` (``max_consecutive_failures`` 3); the
recovery saga restarts it after 5, 10 and 20s and gives up, leaving it ``dead``
for ``given_up`` about a minute in. The retry rightly never starts a given-up
server, and nothing else does, so ``/health/ready`` read 503 for good while
``/health/live`` read 200 -- still 503 half a minute after the backend was back.

The maintainer's rule: ``retry_for_s`` bounds readiness too. Once it has passed,
``/health/ready`` stops looking at the catalogue.

``_catalogue_readiness_harness.py defaults`` changes no failure threshold and no
saga setting; only ``retry_for_s`` is shortened, to 30s, so the window ends
while the saga is still working and the run fits a CI job. The earlier harness
kept its server at ``max_consecutive_failures: 1000``, which is how this was
missed: a server that never degrades never reaches the saga, and never
``given_up``. That setting is still used, in ``shutdown`` mode only, to keep a
server the retry's to start.

Its own module: the saga gives up after about 60-70s, past the 60s timeout the
integration job applies to one test.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_catalogue_readiness_harness.py")
DEFAULTS_WINDOW_S = 30

pytestmark = pytest.mark.timeout(150)


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("catalogue-readiness-defaults") / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), "defaults", str(out)],
        capture_output=True,
        text=True,
        timeout=140,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    return dict(json.loads(out.read_text()))


def test_it_is_held_while_the_window_is_open(run):
    for ready in (run["boot"], run["degraded"]):
        assert ready["status"] == 503, ready
        assert ready["at"] < DEFAULTS_WINDOW_S
        assert ready["body"]["catalogue"]["holds_readiness"] is True


def test_readiness_falls_back_when_the_window_ends(run):
    fallback = run["fallback"]

    assert fallback["status"] == 200, fallback
    assert DEFAULTS_WINDOW_S <= fallback["at"] < DEFAULTS_WINDOW_S + 5
    catalogue = fallback["body"]["catalogue"]
    assert (catalogue["holds_readiness"], catalogue["complete"], catalogue["missing_count"]) == (False, False, 1)
    assert catalogue["retry"] == "exhausted", "the retry must end with the window, in a final state"


def test_the_saga_gives_up_and_readiness_stays_ready(run):
    given_up = run["given_up"]

    assert given_up["state"] == ["dead", "given_up"], given_up
    assert given_up["ready"]["status"] == 200
    assert given_up["ready"]["at"] > DEFAULTS_WINDOW_S, "the give-up came inside the window; nothing was tested"
    catalogue = given_up["ready"]["body"]["catalogue"]
    assert (catalogue["not_retried_count"], catalogue["retry"]) == (1, "exhausted")


def test_nothing_starts_it_once_the_backend_is_back(run):
    back = run["backend_back"]

    assert back["state"] == ["dead", "given_up"]
    assert back["ready"]["status"] == 200
    assert run["attempts_after_fallback"] == 0, "the retry kept starting after its window"
    assert run["starts"] == 0
