"""Regression tests for the project-board caller's triggers and concurrency."""

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "project-board.yml"


def _workflow() -> dict[Any, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text())
    assert isinstance(loaded, dict), f"{WORKFLOW} is not a mapping"
    return loaded


def _triggers(workflow: dict[Any, Any]) -> dict[Any, Any]:
    # `on` is a YAML 1.1 boolean, so PyYAML gives this key back as True. Read
    # both spellings rather than depending on which one the loader produces.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict), "workflow declares no triggers"
    return triggers


def test_project_board_keeps_pull_request_and_issue_runs_in_separate_groups() -> None:
    concurrency = _workflow()["concurrency"]

    assert "github.event.pull_request.number" in concurrency["group"]
    assert "github.event.issue.number" in concurrency["group"]
    # Fallback so the group is never the bare workflow name, which would put
    # unrelated runs in one queue.
    assert "github.ref" in concurrency["group"]


def test_project_board_does_not_cancel_runs_in_progress() -> None:
    """The predecessor cancelled, and this workflow must not.

    `project-add` only ever added an item, so cancelling a superseded run lost
    nothing. This one also moves items between statuses: two labels applied in
    quick succession are two distinct moves, and cancelling the first would
    leave the board holding the status the second was meant to replace.

    A boolean, not the string "false" found anywhere in the file -- a comment
    would satisfy that.
    """
    assert _workflow()["concurrency"]["cancel-in-progress"] is False


def test_project_board_runs_on_pull_request_target() -> None:
    """Not `pull_request`: the App key has to reach the job on a fork's PR."""
    triggers = _triggers(_workflow())

    assert "pull_request_target" in triggers
    assert "pull_request" not in triggers


def test_project_board_watches_every_event_it_dispatches_on() -> None:
    """The reusable branches on these; a missing trigger is a silent no-op."""
    triggers = _triggers(_workflow())

    assert set(triggers["issues"]["types"]) == {
        "opened",
        "reopened",
        "labeled",
        "unlabeled",
    }
    assert set(triggers["pull_request_target"]["types"]) == {
        "opened",
        "reopened",
        "ready_for_review",
    }


def test_project_board_pins_the_reusable_to_a_commit() -> None:
    """A tag or branch ref would let a push elsewhere change this repo's gates."""
    uses = _workflow()["jobs"]["project-board"]["uses"]
    ref = uses.split("@", 1)[1]

    assert len(ref) == 40, uses
    assert all(c in "0123456789abcdef" for c in ref), uses
