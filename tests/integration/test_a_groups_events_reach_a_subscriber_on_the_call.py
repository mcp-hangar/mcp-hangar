"""A group's events reach a subscriber on the call that raised them (#1410).

Runs ``_group_recovery_harness.py`` in a fresh interpreter: the real
``bootstrap()``, ``hangar_call`` through the app ``serve --http`` serves, a
one-member group in front of ``tests/mock_provider.py`` over stdio, and the
health-check worker ``bootstrap()`` created. The subscriber is an ordinary
``subscribe_to_all`` handler on the runtime's own event bus, reading the fields
off each event. Nothing in the harness builds a saga, drains a group by hand,
or sends a CRUD command.

Before this, the only thing that drained a group was the group CRUD handlers.
A member driven out of rotation by calls, and the circuit those failures
opened, were recorded on the aggregate and stayed there: no subscriber heard
them on that call, or on any call, and if nobody edited the group they went out
never and a restart lost them. That is why #1357 had to write its gauge from
the breaker's callback instead.

The harness snapshots what the subscriber has heard after every call, so these
read as deltas: what arrived *during* a given call is what the snapshot taken
after it has and the one before it does not. The earliest snapshot also carries
whatever config loading left on the aggregate, which nothing drained before the
first call either, so the deltas are what carry the claim.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_group_recovery_harness.py")

# As `_group_recovery_harness.py` names them.
MEMBER = "math-a"


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("group-events") / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), "events", str(out)],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    return json.loads(out.read_text())


def _new(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the subscriber heard between the two snapshots.

    ``seen`` only ever grows and a snapshot is a copy of it, so the earlier one
    is a prefix of the later one. Asserted rather than assumed: a slice of two
    lists that had diverged would quietly describe the wrong events.
    """
    assert after[: len(before)] == before, "snapshots are not prefixes of one another"
    return after[len(before) :]


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [event["type"] for event in events]


def test_the_member_starts_in_rotation_and_the_first_call_succeeds(run: dict[str, Any]) -> None:
    [result] = run["calls"]["before"]["results"]

    assert result["success"] is True, run["calls"]["before"]


def test_a_failing_call_that_changes_nothing_publishes_nothing(run: dict[str, Any]) -> None:
    """The first failure counts against the member and crosses no threshold."""
    first = run["failures"][0]

    assert _new(run["events"]["before"], first["events"]) == []
    assert first["status"]["circuit_open"] is False


def test_the_call_that_took_the_member_out_of_rotation_published_it(run: dict[str, Any]) -> None:
    first, second = run["failures"]

    published = _new(first["events"], second["events"])

    left = [e for e in published if e["type"] == "GroupMemberHealthChanged"]
    assert left == [{"type": "GroupMemberHealthChanged", "member_id": MEMBER, "in_rotation": False}], published


def test_and_the_circuit_opening_went_out_on_that_same_call(run: dict[str, Any]) -> None:
    first, second = run["failures"]

    published = _new(first["events"], second["events"])

    assert "GroupCircuitOpened" in _types(published), published
    assert second["status"]["circuit_open"] is True


def test_a_passing_health_check_publishes_the_member_back_and_the_circuit_closed(run: dict[str, Any]) -> None:
    """The other path that raises these events, and the one nothing else was going to publish."""
    published = _new(run["events"]["tripped"], run["events"]["recovered"])

    back = [e for e in published if e["type"] == "GroupMemberHealthChanged"]
    assert back == [{"type": "GroupMemberHealthChanged", "member_id": MEMBER, "in_rotation": True}], published
    assert "GroupCircuitClosed" in _types(published), published
    assert run["status"]["after"]["circuit_open"] is False


def test_no_event_is_published_twice(run: dict[str, Any]) -> None:
    """`collect_events` takes each event once, so a second drain finds nothing to repeat.

    Counted from the first call's snapshot. That call is where the events
    config loading had left sitting on the aggregate were flushed -- nothing
    drained a group at bootstrap before this change either -- so everything
    after it was raised by the two failing calls and the check that followed.
    """
    heard = _types(_new(run["events"]["before"], run["events"]["recovered"]))

    assert heard.count("GroupCircuitOpened") == 1, heard
    assert heard.count("GroupCircuitClosed") == 1, heard
    # Out of rotation on the second failing call, back on the passing check.
    assert heard.count("GroupMemberHealthChanged") == 2, heard


def test_nothing_waited_for_a_crud_command_or_a_rebalance(run: dict[str, Any]) -> None:
    """The harness sends neither. Before this, the events would still be on the aggregate."""
    assert run["rebalances"] == []
    assert run["health_checks_passed"].get(MEMBER, 0) >= 1
