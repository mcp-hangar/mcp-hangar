"""A release PR pushed with GITHUB_TOKEN gets its checks started anyway.

A push authenticated with the built-in `GITHUB_TOKEN` does not trigger
workflows. When the changelog assembly falls back to it, the commit it pushes
becomes the release PR's head and carries no runs, so every required check sits
in "expected" -- not failing, missing -- and the merge is refused even with
`--admin`, because a ruleset requirement is not a branch-protection setting.
That is what happened to the 2.17.1 release PR (#1180): the app id was set, the
token step yielded nothing, and the only warning in the script was guarded on
the app being *unconfigured*, so nothing said a word.

`scripts/refire_release_pr_checks.sh` is the recovery -- close and reopen, which
fires `pull_request: reopened` on the head already there. It lives in its own
file so it can be run here against a stub `gh` rather than only on a release
day.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "refire_release_pr_checks.sh"
_BRANCH = "release-please--branches--main--components--mcp-hangar"


def _stub_gh(tmp_path: Path, *, pr_number: str, reopen_exit: int = 0) -> Path:
    """A `gh` that records its arguments and answers the PR lookup."""
    calls = tmp_path / "calls.log"
    stub = tmp_path / "gh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> {calls}\n'
        'case "$*" in\n'
        f'  *"pr list"*) printf "%s" "{pr_number}" ;;\n'
        f'  *"pr reopen"*) exit {reopen_exit} ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return calls


def _run(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", GITHUB_REPOSITORY="mcp-hangar/mcp-hangar")
    return subprocess.run(
        ["bash", str(_SCRIPT), _BRANCH],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_it_closes_and_reopens_the_release_pr(tmp_path: Path) -> None:
    calls = _stub_gh(tmp_path, pr_number="1173")

    result = _run(tmp_path)

    logged = calls.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "pr close 1173" in logged
    assert "pr reopen 1173" in logged
    assert logged.index("pr close") < logged.index("pr reopen"), "reopening before closing does nothing"


def test_it_looks_the_pr_up_by_the_release_branch(tmp_path: Path) -> None:
    calls = _stub_gh(tmp_path, pr_number="1173")

    _run(tmp_path)

    assert f"--head {_BRANCH}" in calls.read_text(encoding="utf-8")


def test_no_open_pr_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    """Failing here would discard the notes the caller just assembled."""
    _stub_gh(tmp_path, pr_number="")

    result = _run(tmp_path)

    assert result.returncode == 0
    assert "::warning::" in result.stdout


def test_a_failed_reopen_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    _stub_gh(tmp_path, pr_number="1173", reopen_exit=1)

    result = _run(tmp_path)

    assert result.returncode == 0
    assert "by hand" in result.stdout


def test_the_assembler_recovers_on_missing_checks_not_on_a_token_guess() -> None:
    """The condition #1181 shipped could not fire on the run it was written for.

    It compared `PUSH_TOKEN` to `GH_TOKEN`, reasoning that a push made with the
    built-in token does not trigger workflows. The app token was in fact
    available on that run, so the comparison was false and the recovery never
    ran -- while the PR still sat with no checks. What blocks the merge is the
    absence of CHECK RUNS on the head, whatever produced it, so that is what
    the script tests (#1184).
    """
    assembler = (_SCRIPT.parent / "assemble_release_changelog.sh").read_text(encoding="utf-8")

    recovery = assembler[assembler.index("head_sha=$(git rev-parse HEAD)") :]

    assert '"$PUSH_TOKEN" = "$GH_TOKEN"' not in recovery, "the token comparison was the wrong test"
    assert "check-runs" in recovery, "the merge gate reads check runs; so must the recovery"
    assert "refire_release_pr_checks.sh" in assembler, "the assembler must ship the recovery to the runner"


def test_it_says_what_a_human_must_run_when_recovery_is_not_enough() -> None:
    """Reopening uses this job's credential, which is the identity that was
    producing no checks. When the second attempt also comes back empty the run
    has to hand the problem over, with the command rather than a description."""
    assembler = (_SCRIPT.parent / "assemble_release_changelog.sh").read_text(encoding="utf-8")

    assert "::error::" in assembler
    assert "/approve" in assembler, "name the API call, so nobody has to look it up under pressure"


def test_the_assembly_commit_is_authored_by_the_pushing_identity() -> None:
    """The actor decides whether a run needs approval, and the actor is this name.

    The organisation requires approval for "first-time contributors" -- anyone
    with nothing merged into this repository -- and checks the actor of the
    pull request event, not only its author. Committing as
    `github-actions[bot]`, which never has anything merged here (the squash
    into main is attributed to the PR's author, the release app), put every
    release PR's checks in `action_required` (#1184).
    """
    assembler = (_SCRIPT.parent / "assemble_release_changelog.sh").read_text(encoding="utf-8")

    body = assembler[assembler.index("git add -A CHANGELOG.md") :]
    committer = body[: body.index("commit -m")]

    assert "mcp-hangar-release-bot[bot]" in committer, "commit as the app whose token pushes"
    # The fallback identity stays reachable: with no app token, github-actions[bot]
    # really is who pushed, and the history should not claim otherwise.
    assert '"$PUSH_TOKEN" = "$GH_TOKEN"' in committer
