"""The servers a user already has in a client file are read, not just written over (#1770).

`init` wrote its own `mcp-hangar` entry beside the user's servers and never read
them, so they stayed connected straight to the client, outside Hangar. Putting
them behind Hangar starts with reading them. These tests read fixture homes for
the three clients `init` targets -- Claude Code user (`~/.claude.json`), Claude
Code project (`./.mcp.json`) and Cursor (`~/.cursor/mcp.json`) -- found through
the same detection `init` uses.

A client file is the user's, so a malformed entry is reported, never fatal, and
nothing the reader returns or says may carry an `env` or `headers` value.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import pytest

from mcp_hangar.server.cli.services.client_servers import (
    ClientServers,
    EntryProblem,
    RemoteServer,
    StdioServer,
    read_client_servers,
    read_detected,
)
from mcp_hangar.server.cli.services.mcp_clients import McpClient, client_by_key

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "client_homes"
SECRETS = (
    "not-a-real-token-claude-user",
    "not-a-real-token-claude-header",
    "not-a-real-token-cursor",
)


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the fixture home and project; returns the project directory."""
    shutil.copytree(FIXTURES, tmp_path, dirs_exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    return tmp_path / "project"


def _client(key: str, cwd: Path) -> McpClient:
    client = client_by_key(key, cwd=cwd)
    assert client is not None
    return client


def _read(key: str, cwd: Path) -> ClientServers:
    return read_client_servers(_client(key, cwd))


def _by_name(result: ClientServers) -> dict[str, StdioServer | RemoteServer]:
    return {server.name: server for server in result.servers}


class TestEachClientIsRead:
    def test_all_three_are_found_by_init_detection(self, machine: Path) -> None:
        keys = [result.client.key for result in read_detected(cwd=machine)]

        assert keys == ["claude-code", "claude-code-project", "cursor"]

    def test_claude_code_user(self, machine: Path) -> None:
        result = _read("claude-code", machine)

        assert _by_name(result) == {
            "notes": StdioServer(
                "notes", "notes-server", ("--root", "/home/u/notes"), {"NOTES_TOKEN": "not-a-real-token-claude-user"}
            ),
            "search": RemoteServer(
                "search",
                "https://mcp.example.com/mcp",
                "http",
                {"Authorization": "Bearer not-a-real-token-claude-header"},
            ),
        }
        assert result.problems == ()

    def test_claude_code_project(self, machine: Path) -> None:
        result = _read("claude-code-project", machine)

        assert _by_name(result) == {
            "files": StdioServer("files", "npx", ("-y", "files-server", "/home/u/work/app")),
            "tickets": StdioServer("tickets", "uvx", ("tickets-server",), {"TICKETS_API_KEY": "${TICKETS_API_KEY}"}),
            "clock": StdioServer("clock", "clock-server"),
            "events": RemoteServer("events", "https://events.example.com/sse", "sse"),
        }
        assert result.problems == ()

    def test_cursor(self, machine: Path) -> None:
        result = _read("cursor", machine)

        assert _by_name(result) == {
            "browser": StdioServer(
                "browser", "npx", ("browser-server",), {"BROWSER_SECRET": "not-a-real-token-cursor"}
            ),
            "docs": RemoteServer("docs", "https://docs.example.com/mcp"),
        }


class TestWhatIsLeftOut:
    def test_hangars_own_entry_is_not_a_server_or_a_problem(self, machine: Path) -> None:
        result = _read("claude-code", machine)

        assert "mcp-hangar" not in _by_name(result)
        assert all(problem.entry != "mcp-hangar" for problem in result.problems)

    def test_claude_code_per_directory_servers_are_not_user_servers(self, machine: Path) -> None:
        assert "local-only" not in _by_name(_read("claude-code", machine))


class TestMalformedEntriesAreReported:
    def test_every_bad_cursor_entry_is_named_with_a_reason(self, machine: Path) -> None:
        result = _read("cursor", machine)

        reasons = {problem.entry: problem.reason for problem in result.problems}
        assert reasons == {
            "not-an-object": "is not an object",
            "no-launch": "has neither `command` nor `url`",
            "both": "has both `command` and `url`",
            "numeric-env": "`env` is not an object of string values",
            "args-as-string": "`args` is not a list of strings",
            "wrong-type": "declares type 'sse' but has a `command`",
            "ftp": "`url` is not an http(s) URL",
        }
        assert all(problem.client_key == "cursor" for problem in result.problems)

    @pytest.mark.parametrize(
        ("content", "reason"),
        [
            ("{not json", "is not valid JSON (line 1, column 2)"),
            ("[1, 2]", "does not hold a JSON object"),
            ('{"mcpServers": []}', "`mcpServers` is not an object"),
        ],
    )
    def test_a_file_that_cannot_be_read_is_one_problem(self, machine: Path, content: str, reason: str) -> None:
        (machine / ".mcp.json").write_text(content)

        result = _read("claude-code-project", machine)

        assert result.servers == ()
        assert result.problems == (EntryProblem("claude-code-project", machine / ".mcp.json", None, reason),)

    def test_a_file_that_is_not_text_is_one_problem(self, machine: Path) -> None:
        (machine / ".mcp.json").write_bytes(b"\xff\xfe\x00")

        assert [p.reason for p in _read("claude-code-project", machine).problems] == ["is not UTF-8 text"]

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
    def test_an_unreadable_file_is_one_problem(self, machine: Path) -> None:
        path = machine / ".mcp.json"
        path.chmod(0)
        try:
            problems = _read("claude-code-project", machine).problems
        finally:
            path.chmod(0o644)

        assert len(problems) == 1
        assert problems[0].reason.startswith("cannot be read")

    @pytest.mark.parametrize("content", ["", "{}", '{"theme": "dark"}'])
    def test_a_file_with_no_servers_is_empty_not_a_problem(self, machine: Path, content: str) -> None:
        (machine / ".mcp.json").write_text(content)

        assert _read("claude-code-project", machine) == ClientServers(_client("claude-code-project", machine))

    def test_the_file_is_not_changed(self, machine: Path) -> None:
        path = Path.home() / ".cursor" / "mcp.json"
        before = path.read_bytes()

        _read("cursor", machine)

        assert path.read_bytes() == before


@pytest.mark.security
class TestSecretsStayInside:
    def test_no_secret_reaches_a_repr_a_problem_or_the_log(
        self, machine: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)

        results = read_detected(cwd=machine)

        said = [repr(results), *(str(p) for r in results for p in r.problems)]
        said += [repr(s) for r in results for s in r.servers]
        said.append(caplog.text)
        for secret in SECRETS:
            assert not any(secret in text for text in said), secret

    def test_the_secrets_are_still_there_to_import(self, machine: Path) -> None:
        servers = {s.name: s for r in read_detected(cwd=machine) for s in r.servers}

        assert isinstance(servers["notes"], StdioServer)
        assert servers["notes"].env["NOTES_TOKEN"] == SECRETS[0]
        assert isinstance(servers["search"], RemoteServer)
        assert servers["search"].headers["Authorization"].endswith(SECRETS[1])
