"""Every third-party action reference is pinned to a commit, not a tag.

A tag is a movable pointer: `@v4` today and `@v4` tomorrow can be different
code, so a compromised or retagged action reaches CI without a diff to review.
This is also a credibility question rather than only a hygiene one -- Hangar
ships `io.mcp-hangar.digest-pinning` as a governance capability, and its own
pipeline is the first place anybody checks whether we mean it.

Dependabot keeps the pins fresh (`github-actions`, weekly, minor+patch
grouped): it bumps the SHA and rewrites the trailing tag comment, so pinning
does not trade a supply-chain risk for a staleness one.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

USES = re.compile(r"uses:\s+(?P<action>[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+)@(?P<ref>\S+)")

#: Our own org's reusable workflows track `main` on purpose: they carry shared
#: repository policy (PR title, body, branch name), and pinning them would mean
#: every repository drifting to its own copy of the rules.
OWN_REUSABLE = "mcp-hangar/.github"


def test_there_are_workflows_to_check() -> None:
    assert len(WORKFLOWS) > 10


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_by_sha(workflow: pathlib.Path) -> None:
    unpinned = [
        f"{m.group('action')}@{m.group('ref')}"
        for m in USES.finditer(workflow.read_text())
        if not m.group("action").startswith(OWN_REUSABLE) and not re.fullmatch(r"[0-9a-f]{40}", m.group("ref"))
    ]

    assert not unpinned, f"{workflow.name} references {unpinned} by tag; pin to a commit SHA with a `# tag` comment"
