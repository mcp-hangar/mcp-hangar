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


def test_the_assembler_recovers_whenever_it_falls_back(tmp_path: Path) -> None:
    """The condition that let #1180 through: it also guarded on the app being unset.

    An app that is configured and yields no token takes the same fallback, and
    that is the case nobody was warned about.
    """
    assembler = (_SCRIPT.parent / "assemble_release_changelog.sh").read_text(encoding="utf-8")

    guard = assembler[assembler.index('if [ "$PUSH_TOKEN" = "$GH_TOKEN" ]') :]
    condition = guard.splitlines()[0]

    assert "RELEASE_BOT_APP_ID" not in condition, "the fallback is about the token, not about whether an app is set"
    assert "refire_release_pr_checks.sh" in assembler, "the assembler must ship the recovery to the runner"
