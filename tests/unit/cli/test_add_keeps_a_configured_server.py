"""`mcp-hangar add` does not replace a server the config already has (#2042).

It wrote the registry entry over the existing one, so the server's pins, policy,
args and env were dropped without a word, with `-y` not even asked.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from mcp_hangar.server.cli.main import app
from mcp_hangar.server.cli.services import ConfigFileManager, get_mcp_server

NAME = "git"


def _invoke(path: Path, *args: str):
    return CliRunner().invoke(app, ["--config", str(path), "add", NAME, "-y", "--no-reload", *args])


def _pinned(path: Path) -> str:
    entry = ConfigFileManager(path)._build_mcp_server_entry(get_mcp_server(NAME), None, None)
    entry["command"] = [*entry["command"], "--repository", "/srv/repo"]
    entry["tool_projection"] = {"digest_enforcement": "block", "pins": {"git_status": "ab" * 32}}
    text = yaml.safe_dump({"mcp_servers": {NAME: entry}}, sort_keys=False)
    path.write_text(text)
    return text


def test_a_configured_server_is_refused_and_the_file_is_untouched(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    before = _pinned(path)

    result = _invoke(path)

    assert result.exit_code == 1, result.output
    assert "--replace" in result.output
    assert "drops tool_projection" in result.output and "changes command" in result.output
    assert path.read_text() == before


def test_replace_does_it_and_says_what_it_dropped(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    _pinned(path)

    result = _invoke(path, "--replace")

    assert result.exit_code == 0, result.output
    assert "drops tool_projection" in result.output
    entry = yaml.safe_load(path.read_text())["mcp_servers"][NAME]
    assert "tool_projection" not in entry


def test_adding_the_same_server_twice_is_idempotent(tmp_path) -> None:
    path = tmp_path / "config.yaml"

    first = _invoke(path)
    after_first = path.read_text()
    second = _invoke(path)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "already configured" in second.output
    assert path.read_text() == after_first


def test_a_new_server_is_still_added(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("mcp_servers:\n  other:\n    mode: subprocess\n    command: [python]\n")

    result = _invoke(path)

    assert result.exit_code == 0, result.output
    servers = yaml.safe_load(path.read_text())["mcp_servers"]
    assert set(servers) == {"other", NAME}
