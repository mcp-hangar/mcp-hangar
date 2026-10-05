"""Every CODEOWNERS pattern matches at least one tracked file (#1710).

A pattern that matches nothing protects nothing, and nothing says so: four
entries outlived the paths they named (`server/security/`, `docs/adr/`,
`version-bump.yml`, `docs/development/GIT_FLOW.md`).
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _patterns() -> list[str]:
    lines = (REPO / ".github" / "CODEOWNERS").read_text().splitlines()
    return [line.split()[0] for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def _matches(pattern: str, files: list[str]) -> bool:
    if pattern == "*":
        return bool(files)
    anchored = pattern.lstrip("/")
    if anchored.endswith("/"):
        return any(f.startswith(anchored) for f in files)
    return any(fnmatch.fnmatch(f, anchored) or f.startswith(anchored + "/") for f in files)


def test_every_codeowners_pattern_matches_a_tracked_file() -> None:
    files = _tracked_files()
    dead = [p for p in _patterns() if not _matches(p, files)]
    assert dead == [], f"CODEOWNERS patterns that match no tracked file: {dead}"
