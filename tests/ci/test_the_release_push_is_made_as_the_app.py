"""The changelog assembly push reaches GitHub as the release app, not as GITHUB_TOKEN.

On 2026-09-11/12 every assembly push to the release branch was attributed to
`github-actions[bot]`. The commits were signed as the release app, and the app
token was in the push URL (#1379). `actions/checkout` persists the job's
GITHUB_TOKEN as an `http.<server>/.extraheader`, and git sends that header in
preference to the URL's credentials. GitHub starts no workflows for a
GITHUB_TOKEN push, so the release PR's head carried zero check runs, and a
person had to push an empty commit to start CI (2.19.0, 2.19.1).

These tests drive real git against a local HTTP server that records the
`Authorization` header it receives. That header is the credential GitHub
would attribute the push to.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
import http.server
import os
from pathlib import Path
import re
import subprocess
import threading

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_PUSH = _ROOT / "scripts" / "push_release_branch.sh"
_ASSEMBLER = _ROOT / "scripts" / "assemble_release_changelog.sh"
_WORKFLOW = _ROOT / ".github" / "workflows" / "release-please.yml"
_BRANCH = "release-please--branches--main--components--mcp-hangar"
_REPO = "mcp-hangar/mcp-hangar"
_APP_TOKEN = "ghs_app_installation_token"
_GITHUB_TOKEN = "ghs_job_github_token"


def _basic(token: str) -> str:
    return "basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()


class _Recorder(http.server.BaseHTTPRequestHandler):
    """Challenge an anonymous request, record the credential of any other.

    Stopping at 403 once a credential is seen is enough: the question is which
    token git presents, not whether the push completes.
    """

    seen: list[str]

    def _handle(self) -> None:
        auth = self.headers.get("Authorization")
        if auth is None:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="test"')
        else:
            # The scheme is case-insensitive (RFC 7235): checkout writes
            # `basic`, curl sends `Basic`. The credential is what matters.
            scheme, _, credential = auth.partition(" ")
            self.seen.append(f"{scheme.lower()} {credential}")
            self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 -- http.server's handler name
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        self._handle()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        pass


@pytest.fixture
def server() -> Iterator[tuple[str, list[str]]]:
    seen: list[str] = []
    handler = type("Handler", (_Recorder,), {"seen": seen})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", seen
    finally:
        httpd.shutdown()
        httpd.server_close()


def _git_env(home: Path) -> dict[str, str]:
    """Git with no user or system config: no credential helper, no proxy, no prompt."""
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy") and not k.startswith("GIT_")}
    env.update(
        HOME=str(home),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    return env


def _checked_out_repo(tmp_path: Path, server_url: str, persisted: str) -> tuple[Path, dict[str, str]]:
    """A repository with one commit, holding GITHUB_TOKEN the way checkout leaves it."""
    work = (tmp_path / "work").resolve()
    work.mkdir()
    env = _git_env(tmp_path)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=work, env=env, check=True, capture_output=True)

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "chore(release): assemble changelog")

    key = f"http.{server_url}/.extraheader"
    value = f"AUTHORIZATION: {_basic(_GITHUB_TOKEN)}"
    if persisted == "local":
        # actions/checkout up to v5: straight into .git/config.
        git("config", "--local", key, value)
    elif persisted == "includeIf":
        # actions/checkout v6+ (v7 on the Release Please run 34689820079): a
        # credentials file pulled in by `includeIf.gitdir:`.
        creds = tmp_path / "git-credentials.config"
        subprocess.run(["git", "config", "--file", str(creds), key, value], env=env, check=True)
        git("config", "--local", f"includeIf.gitdir:{work}/.git.path", str(creds))
    return work, env


@pytest.mark.parametrize("persisted", ["local", "includeIf"])
def test_a_token_in_the_url_loses_to_the_header_checkout_persisted(
    tmp_path: Path, server: tuple[str, list[str]], persisted: str
) -> None:
    """The control: the push line the assembler used until #1379, against the same setup.

    It is what put `github-actions[bot]` on every assembly push. If git ever
    stops preferring the header, this fails, and the reset in
    push_release_branch.sh can be reconsidered.
    """
    url, seen = server
    work, env = _checked_out_repo(tmp_path, url, persisted)
    host = url.removeprefix("http://")

    subprocess.run(
        ["git", "push", f"http://x-access-token:{_APP_TOKEN}@{host}/{_REPO}.git", f"HEAD:{_BRANCH}"],
        cwd=work,
        env=env,
        capture_output=True,
        check=False,
    )

    assert seen, "git presented no credential at all"
    assert set(seen) == {_basic(_GITHUB_TOKEN)}, f"expected only the persisted GITHUB_TOKEN, saw {seen}"


@pytest.mark.parametrize("persisted", ["none", "local", "includeIf"])
def test_the_push_presents_only_the_app_token(tmp_path: Path, server: tuple[str, list[str]], persisted: str) -> None:
    url, seen = server
    work, env = _checked_out_repo(tmp_path, url, persisted)
    env.update(PUSH_TOKEN=_APP_TOKEN, GITHUB_REPOSITORY=_REPO, GITHUB_SERVER_URL=url)

    result = subprocess.run(
        ["bash", str(_PUSH), _BRANCH], cwd=work, env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode != 0, "the recorder refuses every push; a zero exit means git never reached it"
    assert seen, f"git presented no credential at all: {result.stderr}"
    assert set(seen) == {_basic(_APP_TOKEN)}, f"expected only the app token, saw {seen}"


def test_the_push_refuses_to_run_without_a_token(tmp_path: Path) -> None:
    env = _git_env(tmp_path)

    result = subprocess.run(["bash", str(_PUSH), _BRANCH], env=env, capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert "PUSH_TOKEN" in result.stderr


def _code_lines(script: Path) -> list[str]:
    return [line for line in script.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]


def test_the_assembler_pushes_only_through_the_helper() -> None:
    """A bare `git push` in the assembler would reintroduce the persisted-header push."""
    code = _code_lines(_ASSEMBLER)

    assert not [line for line in code if re.search(r"\bgit\b.*\bpush\b", line)], "push through push_release_branch.sh"
    assert any("push_release_branch.sh" in line for line in code)
    assert any(re.search(r'bash "\$\{?pusher\}?" "\$branch"', line) for line in code)


def test_the_release_checkout_persists_no_credential() -> None:
    """Nothing in the job needs git to hold GITHUB_TOKEN: release-please writes through
    the API, and the repository is public, so the assembler's fetch needs no credential.
    A credential that is not persisted cannot win over the app token."""
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["release-please"]["steps"]
    checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]

    assert checkouts, "the job checks the tree out"
    for step in checkouts:
        assert step.get("with", {}).get("persist-credentials") is False
