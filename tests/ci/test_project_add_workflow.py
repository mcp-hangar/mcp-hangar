"""Regression tests for the project-add workflow's event-specific concurrency."""

from pathlib import Path
import re


WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "project-add.yml"


def _concurrency_group_expression(workflow: str) -> str:
    match = re.search(r"^\s+group:\s+\$\{\{\s*(.*?)\s*\}\}\s*$", workflow, re.MULTILINE)
    assert match is not None, "project-add must define a concurrency group expression"
    return match.group(1)


def test_project_add_keeps_pull_request_runs_in_separate_groups() -> None:
    workflow = WORKFLOW.read_text()
    expression = _concurrency_group_expression(workflow)

    assert "pull_request_target:" in workflow
    assert "github.event.pull_request.number" in expression
    assert "github.event.issue.number" in expression
    assert "github.ref" in expression
    assert "cancel-in-progress: true" in workflow
