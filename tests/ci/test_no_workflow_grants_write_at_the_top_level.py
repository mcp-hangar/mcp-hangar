"""No workflow hands every job a write token (OpenSSF Token-Permissions).

A top-level `permissions:` block is inherited by every job in the file, so one
step that needs to push a tag makes every other step in that workflow able to
push one too. Scorecard scores this bluntly and correctly: any workflow-level
write of a sensitive scope takes Token-Permissions to 0 no matter how careful
the rest of the repository is.

Job-level grants are not checked here. They are the correct shape -- the job
that writes declares it -- and Scorecard treats them as such.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

#: The scopes Scorecard treats as sensitive: a token holding any of them can
#: alter the repository's contents or its published artifacts.
SENSITIVE = ("contents", "packages", "actions", "id-token", "deployments", "security-events")


def test_there_are_workflows_to_check() -> None:
    """Guards the glob: an empty list would make every assertion below vacuous."""
    assert len(WORKFLOWS) > 10


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_the_top_level_permissions_grant_no_sensitive_write(workflow: pathlib.Path) -> None:
    declared = yaml.safe_load(workflow.read_text()).get("permissions")

    if declared is None:
        # No block at all means the repository default applies, which is a
        # setting rather than a file, so this file cannot be judged here.
        return
    if isinstance(declared, str):
        assert declared != "write-all", f"{workflow.name} grants write-all at the top level"
        return
    granted = [scope for scope in SENSITIVE if declared.get(scope) == "write"]
    assert not granted, f"{workflow.name} grants {granted} at the top level; move the grant onto the job that needs it"
