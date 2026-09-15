"""A caller's own error does not count against a group member, and the member's own failures still do (#1409).

Each mode runs ``_group_recovery_harness.py`` in a fresh interpreter: the real
``bootstrap()``, ``hangar_call`` through the app ``serve --http`` serves, and a
one-member group in front of ``tests/mock_provider.py`` over stdio. The group
is as strict as the recovery tests make it: two counted failures take the
member out of rotation and open the circuit.

Before #1409 every call through a group that did not succeed counted against
the member. A division by zero, arguments the tool cannot read, or a result the
tool marks ``isError`` each answer the request correctly, and two of them took a
healthy member out. So did two calls the command bus's rate limit refused, which
never reached the member at all.

``caller`` sends each such call three times, reading the group after every one,
then makes the upstream fail two calls, which still take the member out.
``rate_limited`` puts the group behind a rate limit that admits one call.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_group_recovery_harness.py")
MODES = ("caller", "rate_limited")

# As `_group_recovery_harness.py` names them.
MEMBER, REPEATS = "math-a", 3
#: Each caller error, and a piece of what the upstream answered.
CALLER_ERRORS = {
    "application_error": "division by zero",
    "invalid_params": "Invalid params",
    "tool_error": "cannot be raised to a negative power",
}


def _run(mode: str, tmp: Path) -> dict[str, Any]:
    out = tmp / mode / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(out)],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{mode}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return json.loads(out.read_text())


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    # Concurrently, under the 60s pytest-timeout the integration job applies.
    tmp = tmp_path_factory.mktemp("caller-errors")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


def _outcome(batch: dict[str, Any]) -> dict[str, Any]:
    [result] = batch["results"]
    return result


def _member(status: dict[str, Any]) -> dict[str, Any]:
    return next(m for m in status["members"] if m["id"] == MEMBER)


def _untouched(steps: list[dict[str, Any]]) -> None:
    """After every step the member is in rotation with no failure counted, and the circuit is closed."""
    assert len(steps) == REPEATS
    for number, step in enumerate(steps, start=1):
        status = step["status"]
        member = _member(status)
        assert member["in_rotation"] is True and member["consecutive_failures"] == 0, (number, status)
        assert status["circuit_open"] is False and status["healthy_count"] == 1, (number, status)


@pytest.mark.parametrize("mode", MODES)
def test_the_first_call_through_the_group_succeeds(runs, mode):
    assert _outcome(runs[mode]["calls"]["before"])["success"] is True, runs[mode]["calls"]["before"]


# ----------------------------------------------------------------------------
# The upstream answered the request: the member is working.
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(CALLER_ERRORS))
def test_the_upstream_answered_the_callers_error(runs, kind):
    for step in runs["caller"]["caller"][kind]:
        outcome = _outcome(step["call"])
        assert outcome["success"] is False and outcome["error_type"] == "ToolInvocationError", outcome
        assert CALLER_ERRORS[kind] in outcome["error"], outcome


@pytest.mark.parametrize("kind", sorted(CALLER_ERRORS))
def test_a_callers_error_leaves_the_member_in_rotation_and_the_circuit_closed(runs, kind):
    _untouched(runs["caller"]["caller"][kind])


# ----------------------------------------------------------------------------
# The upstream failed: the member is not working, as before.
# ----------------------------------------------------------------------------


def test_the_upstream_failing_still_takes_the_member_out_and_opens_the_circuit(runs):
    first, second = runs["caller"]["failures"]

    assert all(_outcome(step["call"])["success"] is False for step in (first, second))
    assert _member(first["status"])["consecutive_failures"] == 1, first["status"]
    assert _member(first["status"])["in_rotation"] is True and first["status"]["circuit_open"] is False, first
    assert _member(second["status"])["consecutive_failures"] == 2, second["status"]
    assert _member(second["status"])["in_rotation"] is False and second["status"]["circuit_open"] is True, second


def test_the_group_then_refuses(runs):
    refused = _outcome(runs["caller"]["calls"]["refused"])

    assert refused["error_type"] == "NoAvailableMemberError", refused


# ----------------------------------------------------------------------------
# Hangar refused the call before asking the member: nothing to count.
# ----------------------------------------------------------------------------


def test_the_rate_limit_refuses_the_calls_after_the_first(runs):
    for step in runs["rate_limited"]["refused"]:
        outcome = _outcome(step["call"])
        assert outcome["success"] is False and outcome["error_type"] == "RateLimitExceeded", outcome


def test_a_rate_limit_refusal_leaves_the_member_in_rotation_and_the_circuit_closed(runs):
    _untouched(runs["rate_limited"]["refused"])
