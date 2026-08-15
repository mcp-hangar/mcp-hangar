"""Regression tests for the project-add workflow's event-specific concurrency."""

from pathlib import Path

import yaml


WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "project-add.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def test_project_add_keeps_pull_request_runs_in_separate_groups() -> None:
    concurrency = _workflow()["concurrency"]

    assert "github.event.pull_request.number" in concurrency["group"]
    assert "github.event.issue.number" in concurrency["group"]
    assert "github.ref" in concurrency["group"]
    # A boolean, not the string "true" found anywhere in the file -- the previous
    # form asserted `"cancel-in-progress: true" in workflow`, which a comment
    # would have satisfied.
    assert concurrency["cancel-in-progress"] is True


def test_project_add_runs_on_pull_request_target() -> None:
    workflow = _workflow()

    # `on` is a YAML 1.1 boolean, so PyYAML gives this key back as True. Read
    # both spellings rather than depending on which one the loader produces.
    triggers = workflow.get("on", workflow.get(True))

    assert "pull_request_target" in triggers
