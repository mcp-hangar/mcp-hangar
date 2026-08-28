"""The changelog gate must not hold a dependency bump hostage.

`changelog.d/README.md` says a `chore(deps)` PR owes no fragment. The gate did
not implement that: a Python bump edits `pyproject.toml`, which is a triggering
path, so every dependabot PR failed the required `changelog / check` and sat
red until a human applied `skip-changelog` by hand. These run the real script.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_changelog.sh"


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A two-commit repo whose second commit edits a triggering path."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["httpx==1.2.3"]\n')
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "bump")
    return tmp_path


def _check(repo: pathlib.Path, title: str, labels: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=repo,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "BASE_SHA": _git(repo, "rev-parse", "HEAD~1"),
            "HEAD_SHA": _git(repo, "rev-parse", "HEAD"),
            "PR_LABELS": labels,
            "PR_TITLE": title,
        },
    )


@pytest.mark.parametrize(
    "title",
    [
        "chore(deps): bump the python-minor-patch group across 1 directory with 4 updates",
        "ci(deps): bump github/codeql-action from 4.37.7 to 4.37.8 in the github-actions group",
        "build(deps-dev): bump ruff",
    ],
)
def test_a_dependency_bump_needs_no_fragment(repo: pathlib.Path, title: str) -> None:
    result = _check(repo, title)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Dependency bump" in result.stdout


def test_an_ordinary_change_to_a_triggering_path_still_owes_one(repo: pathlib.Path) -> None:
    """The gate is not weakened for anything but a bump."""
    result = _check(repo, "feat(core): add a thing")

    assert result.returncode == 1
    assert "No changelog fragment added" in result.stderr + result.stdout


def test_a_feature_wearing_a_deps_word_is_not_a_bump(repo: pathlib.Path) -> None:
    """The match is anchored on the Conventional Commit scope, not the word."""
    result = _check(repo, "feat(core): rework how deps are resolved")

    assert result.returncode == 1
