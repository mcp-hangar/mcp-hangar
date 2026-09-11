"""The changelog gate judges a PR by what the PR changed, not by where main is now.

The workflow passes `github.event.pull_request.base.sha`, the base branch TIP.
A two-dot diff from it to the head counts everything main did since the branch
point as the PR's own (#1337): a test-only PR owed a fragment for main's `src/`
changes (#1312, #1335), and a release deleting the pending fragments on main
made them look ADDED by every branch cut before it. The dependency-bump tests
beside this file build a linear history, where the base tip and the merge base
are the same commit, so they could not see any of this.

These run the real script against real diverged histories. The working tree is
what actions/checkout puts on disk for a `pull_request` event -- the PR merged
into the base tip, `refs/pull/N/merge` -- because `build_changelog.py check`
reads the fragments from it.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_changelog.sh"
ASSEMBLER = ROOT / "scripts" / "build_changelog.py"

# `build_changelog.py` needs 3.11 (`datetime.UTC`); a system `python3` may be older.
SEARCH_PATH = os.pathsep.join([str(pathlib.Path(sys.executable).parent), "/usr/bin", "/bin", "/usr/local/bin"])

EARLIER = "changelog.d/100-earlier.fixed.md"
OTHER = "changelog.d/200-other.added.md"
MINE = "changelog.d/300-mine.fixed.md"


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: pathlib.Path, branch: str | None, files: dict[str, str | None]) -> None:
    """Commit on `branch` (None: the current one): write each file, or delete it where the text is None."""
    if branch is not None:
        _git(repo, "checkout", "-q", branch)
    for name, text in files.items():
        target = repo / name
        if text is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"on {branch}")


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A `main` holding an earlier PR's pending fragment, with `pr` cut from it."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "scripts").mkdir()
    shutil.copy(ASSEMBLER, tmp_path / "scripts" / "build_changelog.py")
    _commit(
        tmp_path,
        None,
        {
            "src/app.py": "VALUE = 1\n",
            "src/other.py": "OTHER = 1\n",
            "tests/test_app.py": "def test_app():\n    pass\n",
            "CHANGELOG.md": "# Changelog\n",
            EARLIER: "An earlier PR's entry, pending release.\n",
        },
    )
    _git(tmp_path, "branch", "pr")
    return tmp_path


def _release(repo: pathlib.Path) -> None:
    """What `build_changelog.py assemble` lands on main: CHANGELOG.md grows, the fragments go."""
    _commit(repo, "main", {"CHANGELOG.md": "# Changelog\n\n## [1.0.0]\n\n- An earlier PR's entry\n", EARLIER: None})


def _run(cwd: pathlib.Path, base: str, head: str, title: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={"PATH": SEARCH_PATH, "BASE_SHA": base, "HEAD_SHA": head, "PR_LABELS": "", "PR_TITLE": title},
    )


def _check(repo: pathlib.Path, title: str, *, tree: str = "merge") -> subprocess.CompletedProcess[str]:
    """Run the gate for `pr` against the tip of `main`, as the workflow does."""
    base = _git(repo, "rev-parse", "main")
    head = _git(repo, "rev-parse", "pr")
    if tree == "merge":
        _git(repo, "checkout", "-q", "--detach", base)
        _git(repo, "merge", "-q", "--no-ff", "--no-edit", head)
    else:
        _git(repo, "checkout", "-q", "--detach", head)
    return _run(repo, base, head, title)


def test_a_stale_test_only_pr_owes_no_fragment_for_mains_changes(repo: pathlib.Path) -> None:
    """#1312 and #1335: main changed `src/` after the branch point, the PR only `tests/`."""
    _commit(repo, "pr", {"tests/test_app.py": "def test_app():\n    assert True\n"})
    _commit(repo, "main", {"src/other.py": "OTHER = 2\n"})

    result = _check(repo, "fix(core): stop a flaky test")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "No triggering files changed" in result.stdout


# `merge` is the tree the workflow checks out. `head` is the PR alone, as when
# the gate runs against a checkout of the branch itself.
@pytest.mark.parametrize("tree", ["merge", "head"])
def test_a_pr_cut_before_a_release_still_owes_its_own_fragment(repo: pathlib.Path, tree: str) -> None:
    """The release deleted the pending fragment on main; the PR still carries it, but did not add it."""
    _commit(repo, "pr", {"src/app.py": "VALUE = 2\n"})
    _release(repo)

    result = _check(repo, "feat(core): add a thing", tree=tree)
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "No changelog fragment added" in output
    assert EARLIER not in output
    # The release edited CHANGELOG.md, not this PR.
    assert "CHANGELOG.md is generated" not in output


def test_only_the_fragment_the_pr_added_is_its_entry(repo: pathlib.Path) -> None:
    """Main released and merged another PR's fragment; neither is this PR's."""
    _commit(repo, "pr", {"src/app.py": "VALUE = 2\n", MINE: "This PR's own entry.\n"})
    _release(repo)
    _commit(repo, "main", {"src/other.py": "OTHER = 2\n", OTHER: "Another PR's entry.\n"})

    result = _check(repo, "fix(core): fix a thing")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Changelog fragment found:" in result.stdout
    listed = result.stdout.split("Changelog fragment found:", 1)[1].split()
    assert listed == [MINE]


def test_a_pr_that_edits_changelog_md_is_still_warned(repo: pathlib.Path) -> None:
    _commit(repo, "pr", {"CHANGELOG.md": "# Changelog\n\n- hand-written\n"})
    _commit(repo, "main", {"src/other.py": "OTHER = 2\n"})

    result = _check(repo, "docs(repo): note a thing")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning file=CHANGELOG.md::" in result.stdout


def test_unrelated_histories_are_a_named_error_not_a_pass(repo: pathlib.Path) -> None:
    _git(repo, "checkout", "-q", "--orphan", "unrelated")
    (repo / "tests" / "test_app.py").write_text("def test_app():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated root")

    result = _run(repo, _git(repo, "rev-parse", "main"), _git(repo, "rev-parse", "unrelated"), "fix(core): x")
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "::error::Cannot resolve the merge base" in output
    assert "No triggering files changed" not in output


def test_a_shallow_clone_is_a_named_error_not_a_pass(
    repo: pathlib.Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The case the workflow's `fetch-depth: 0` exists for: both tips present, their history not."""
    _commit(repo, "pr", {"tests/test_app.py": "def test_app():\n    assert True\n"})
    _commit(repo, "main", {"README.md": "docs\n"})
    clone = tmp_path_factory.mktemp("shallow")
    _git(clone, "clone", "-q", "--depth", "1", "--no-single-branch", repo.as_uri(), ".")

    base = _git(clone, "rev-parse", "origin/main")
    head = _git(clone, "rev-parse", "origin/pr")
    result = _run(clone, base, head, "fix(core): x")
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "::error::Cannot resolve the merge base" in output
    assert "No triggering files changed" not in output
