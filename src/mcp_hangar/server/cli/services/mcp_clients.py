"""Write the Hangar entry into the MCP clients on this machine (#1192).

`init` knew one client: Claude Desktop. The people this project is for are
mostly not in Claude Desktop -- they have five to fifteen servers in a
`.mcp.json` or a `~/.cursor/mcp.json`, which is where a governed local run has
to appear or it does not exist for them.

Every client here stores the same thing: a `mcpServers` object mapping a name to
a stdio launch. The differences are the file path and whether the entry carries
an explicit `type`, so one writer covers all of them and each client is a row of
data rather than a class.

The entry is **merged**, never substituted. An earlier version of the Desktop
writer replaced the whole `mcpServers` map with Hangar's single entry, so a user
with eight servers configured and one selected in the wizard lost the other
seven -- silently, on a file the wizard had just backed up but never mentioned
again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import platform
import shutil

HANGAR_ENTRY_NAME = "mcp-hangar"


@dataclass(frozen=True)
class McpClient:
    """One place on disk that lists MCP servers for a client."""

    key: str
    label: str
    path: Path
    #: Claude Code declares the transport explicitly; Cursor and Claude Desktop
    #: infer stdio from the presence of `command`.
    declares_type: bool = False
    #: A project-scope file is written where the user is standing, so it is
    #: offered only when it already exists -- creating one in whatever directory
    #: `init` happened to run in is not a favour.
    project_scope: bool = False


def known_clients(cwd: Path | None = None) -> list[McpClient]:
    """Every client file this machine could have, whether or not it exists."""
    home = Path.home()
    here = cwd or Path.cwd()

    clients = [
        McpClient("claude-code", "Claude Code (user)", home / ".claude.json", declares_type=True),
        McpClient(
            "claude-code-project", "Claude Code (project)", here / ".mcp.json", declares_type=True, project_scope=True
        ),
        McpClient("cursor", "Cursor (user)", home / ".cursor" / "mcp.json"),
        McpClient("cursor-project", "Cursor (project)", here / ".cursor" / "mcp.json", project_scope=True),
    ]

    desktop = _claude_desktop_config_path()
    if desktop is not None:
        clients.insert(0, McpClient("claude-desktop", "Claude Desktop", desktop))
    return clients


def detect_clients(cwd: Path | None = None) -> list[McpClient]:
    """The client files that actually exist here."""
    return [client for client in known_clients(cwd) if client.path.is_file()]


def client_by_key(key: str, cwd: Path | None = None) -> McpClient | None:
    for client in known_clients(cwd):
        if client.key == key:
            return client
    return None


def write_hangar_entry(client: McpClient, hangar_config_path: Path) -> Path | None:
    """Point *client* at this Hangar, keeping everything else in its file.

    Returns the backup path, or None when there was no file to back up (a user
    who has Cursor installed but has never configured a server).
    """
    document: dict = {}
    backup: Path | None = None

    if client.path.is_file():
        backup = _backup(client.path)
        try:
            document = json.loads(client.path.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError:
            # A file we cannot parse is a file we must not rewrite: the backup
            # would be the only copy of something the user still needs.
            raise

    servers = dict(document.get("mcpServers") or {})
    entry: dict[str, object] = {
        "command": HANGAR_ENTRY_NAME,
        "args": ["--config", str(hangar_config_path), "serve"],
    }
    if client.declares_type:
        entry = {"type": "stdio", **entry}
    servers[HANGAR_ENTRY_NAME] = entry
    document["mcpServers"] = servers

    client.path.parent.mkdir(parents=True, exist_ok=True)
    client.path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return backup


def _backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(f".backup.{stamp}{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def _claude_desktop_config_path() -> Path | None:
    """Where Claude Desktop keeps its config on this platform, if anywhere."""
    home = Path.home()
    candidates = {
        "Darwin": home / "Library" / "Application Support" / "Claude",
        "Linux": home / ".config" / "claude",
        "Windows": home / "AppData" / "Roaming" / "Claude",
    }
    base = candidates.get(platform.system())
    if base is None:
        return None
    return base / "claude_desktop_config.json"
