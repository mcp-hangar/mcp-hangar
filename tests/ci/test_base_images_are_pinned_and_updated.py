"""Every base image is pinned by digest, and something updates the pins.

Half of this is the usual supply-chain argument: `python:3.14-slim` is a
movable tag, so two builds of the same commit can differ. The other half is the
trap that makes a naive fix worse than the problem -- a digest with no updater
behind it is a base image quietly accumulating CVEs. So the pin and the
Dependabot `docker` ecosystem are asserted together: neither is correct alone.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEPENDABOT = ROOT / ".github" / "dependabot.yml"

FROM = re.compile(
    r"^FROM\s+(?P<image>\S+)(?:\s+AS\s+(?P<stage>\S+))?", re.MULTILINE
)  # case-sensitive: a heredoc in a Dockerfile can hold Python `from x import y`

#: Trees that are not ours to pin: dependencies, and the agent worktrees that
#: hold a second checkout of this same repository.
EXCLUDED = (".venv", "node_modules", ".git", ".claude")


def _dockerfiles() -> list[pathlib.Path]:
    return sorted(
        path
        for path in ROOT.rglob("Dockerfile*")
        if path.is_file() and not any(part in EXCLUDED for part in path.parts)
    )


DOCKERFILES = _dockerfiles()


def test_there_are_dockerfiles_to_check() -> None:
    """Guards the glob: an empty list would make the assertions below vacuous."""
    assert len(DOCKERFILES) >= 9


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_base_image_is_pinned_by_digest(dockerfile: pathlib.Path) -> None:
    matches = list(FROM.finditer(dockerfile.read_text()))
    # A later stage building on an earlier one names the stage, not an image.
    stages = {m.group("stage") for m in matches if m.group("stage")}
    unpinned = [
        m.group("image") for m in matches if "@sha256:" not in m.group("image") and m.group("image") not in stages
    ]

    assert not unpinned, f"{dockerfile.relative_to(ROOT)} builds on {unpinned} by tag; pin with @sha256:"


def test_dependabot_updates_every_directory_that_holds_a_dockerfile() -> None:
    """A pin nobody bumps is a frozen CVE, so the updater is part of the fix."""
    config = yaml.safe_load(DEPENDABOT.read_text())
    covered = set()
    for update in config["updates"]:
        if update["package-ecosystem"] != "docker":
            continue
        covered.update(update.get("directories") or [update["directory"]])

    # `relative_to` gives "." for a Dockerfile at the root and a bare path
    # otherwise. Stripping a leading "." handled the first case and mangled the
    # second: a dotted directory like `.clusterfuzzlite` came out as
    # `/clusterfuzzlite`, which matches no dependabot entry and no directory.
    needed = set()
    for path in DOCKERFILES:
        relative = path.parent.relative_to(ROOT)
        needed.add("/" if relative == pathlib.Path(".") else f"/{relative}")

    assert needed <= covered, f"no docker dependabot entry for {sorted(needed - covered)}"
