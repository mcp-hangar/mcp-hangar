"""Pointing an MCP client at Hangar keeps everything else in its file (#1192).

`init` knew one client, Claude Desktop, and its writer set `mcpServers` to a
dict containing only the Hangar entry. A user with eight servers configured lost
seven of them -- on a file the wizard had just backed up and never mentioned
again. Claude Code and Cursor, where most of the people this is for actually
work, were not written at all.

The paths and key names here were read off each client's current documentation
rather than recalled: Claude Code takes `~/.claude.json` (user) and `.mcp.json`
(project) and declares `type: stdio`; Cursor takes `~/.cursor/mcp.json` and
`.cursor/mcp.json` and infers the transport from `command`.
"""

import json
from pathlib import Path

import pytest

from mcp_hangar.server.cli.services.mcp_clients import (
    HANGAR_ENTRY_NAME,
    client_by_key,
    detect_clients,
    known_clients,
    write_hangar_entry,
)

HANGAR_CONFIG = Path("/home/u/.mcp-hangar/config.yaml")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return client_by_key("cursor", cwd=tmp_path)


class TestWhatIsWritten:
    def test_an_existing_server_survives(self, client):
        client.path.parent.mkdir(parents=True, exist_ok=True)
        client.path.write_text(json.dumps({"mcpServers": {"mine": {"command": "my-server"}}}))

        write_hangar_entry(client, HANGAR_CONFIG)

        servers = json.loads(client.path.read_text())["mcpServers"]
        assert servers["mine"] == {"command": "my-server"}
        assert servers[HANGAR_ENTRY_NAME]["args"] == ["--config", str(HANGAR_CONFIG), "serve"]

    def test_unrelated_settings_survive(self, client):
        client.path.parent.mkdir(parents=True, exist_ok=True)
        client.path.write_text(json.dumps({"theme": "dark", "mcpServers": {}}))

        write_hangar_entry(client, HANGAR_CONFIG)

        assert json.loads(client.path.read_text())["theme"] == "dark"

    def test_a_missing_file_is_created(self, client):
        assert not client.path.exists()

        backup = write_hangar_entry(client, HANGAR_CONFIG)

        assert backup is None  # nothing to back up
        assert HANGAR_ENTRY_NAME in json.loads(client.path.read_text())["mcpServers"]

    def test_the_previous_file_is_kept(self, client):
        client.path.parent.mkdir(parents=True, exist_ok=True)
        client.path.write_text(json.dumps({"mcpServers": {"mine": {"command": "my-server"}}}))

        backup = write_hangar_entry(client, HANGAR_CONFIG)

        assert backup is not None
        assert json.loads(backup.read_text())["mcpServers"] == {"mine": {"command": "my-server"}}

    def test_an_unreadable_file_is_not_rewritten(self, client):
        client.path.parent.mkdir(parents=True, exist_ok=True)
        client.path.write_text("{not json")

        with pytest.raises(json.JSONDecodeError):
            write_hangar_entry(client, HANGAR_CONFIG)

        assert client.path.read_text() == "{not json"


class TestPerClientShape:
    def test_claude_code_declares_the_transport(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        client = client_by_key("claude-code", cwd=tmp_path)

        write_hangar_entry(client, HANGAR_CONFIG)

        entry = json.loads(client.path.read_text())["mcpServers"][HANGAR_ENTRY_NAME]
        assert entry["type"] == "stdio"

    def test_cursor_does_not(self, client):
        write_hangar_entry(client, HANGAR_CONFIG)

        entry = json.loads(client.path.read_text())["mcpServers"][HANGAR_ENTRY_NAME]
        assert "type" not in entry

    def test_the_known_paths_are_the_documented_ones(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        paths = {c.key: c.path for c in known_clients(cwd=tmp_path / "project")}

        assert paths["claude-code"] == tmp_path / ".claude.json"
        assert paths["claude-code-project"] == tmp_path / "project" / ".mcp.json"
        assert paths["cursor"] == tmp_path / ".cursor" / "mcp.json"
        assert paths["cursor-project"] == tmp_path / "project" / ".cursor" / "mcp.json"


class TestDetection:
    def test_only_files_that_exist_are_detected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        (tmp_path / ".claude.json").write_text("{}")

        detected = [c.key for c in detect_clients(cwd=tmp_path)]

        assert detected == ["claude-code"]
