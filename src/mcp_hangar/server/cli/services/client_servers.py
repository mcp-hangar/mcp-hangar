"""Read the servers a detected MCP client already has (#1770, stage 1).

`init` only ever wrote into a client's `mcpServers` map; nothing read it. To put
the user's own servers behind Hangar, the first step is knowing what they are,
so this module turns each detected client file into a typed list: stdio entries
(`command`/`args`/`env`) and remote entries (`url`). Nothing here writes.

A file belongs to the user, not to us, so nothing in it is allowed to crash the
reader. An entry we cannot make sense of becomes an `EntryProblem` naming the
client, the entry and the reason -- never the entry's values, because `env` and
`headers` are where people keep tokens.

Not yet wired into `init`; importing, pinning and rewriting the client file are
later stages of #1770.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .mcp_clients import HANGAR_ENTRY_NAME, McpClient, detect_clients

RemoteTransport = Literal["http", "sse"]

#: `type` values a remote entry may declare. Claude Code writes `http` or `sse`;
#: `streamable-http` is the transport's spec name and means the same as `http`.
_REMOTE_TYPES: dict[str, RemoteTransport] = {"http": "http", "streamable-http": "http", "sse": "sse"}


@dataclass(frozen=True)
class StdioServer:
    """A server the client launches as a child process."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    #: Verbatim, `${VAR}` references included. Kept out of `repr` because the
    #: values are often secrets.
    env: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class RemoteServer:
    """A server the client reaches over the network."""

    name: str
    url: str
    #: None when the entry does not say; Cursor infers it from the URL.
    transport: RemoteTransport | None = None
    #: Kept out of `repr` for the same reason as `env`: `Authorization` lives here.
    headers: dict[str, str] = field(default_factory=dict, repr=False)


ClientServer = StdioServer | RemoteServer


@dataclass(frozen=True)
class EntryProblem:
    """Something in a client file that was not read. `entry` is None for the file itself."""

    client_key: str
    path: Path
    entry: str | None
    reason: str

    def __str__(self) -> str:
        where = f"{self.path} entry {self.entry!r}" if self.entry is not None else str(self.path)
        return f"{where}: {self.reason}"


@dataclass(frozen=True)
class ClientServers:
    """What one client file lists, and what in it could not be read."""

    client: McpClient
    servers: tuple[ClientServer, ...] = ()
    problems: tuple[EntryProblem, ...] = ()


def read_detected(cwd: Path | None = None) -> list[ClientServers]:
    """The servers of every client file that exists here, one result per file."""
    return [read_client_servers(client) for client in detect_clients(cwd)]


def read_client_servers(client: McpClient) -> ClientServers:
    """Read the top-level `mcpServers` map of *client*'s file.

    Only the top level is read. `~/.claude.json` also keeps per-directory
    servers under `projects.<path>.mcpServers`; those are not this file's
    user-scope servers and are left for a later stage to decide on.

    The `mcp-hangar` entry is skipped without a problem: it is Hangar itself.
    """

    def file_problem(reason: str) -> ClientServers:
        return ClientServers(client, problems=(EntryProblem(client.key, client.path, None, reason),))

    try:
        text = client.path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return ClientServers(client)
    except OSError as exc:
        return file_problem(f"cannot be read ({exc.strerror or type(exc).__name__})")
    except UnicodeDecodeError:
        return file_problem("is not UTF-8 text")

    if not text.strip():
        return ClientServers(client)
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        # The message carries a position, not the file's content.
        return file_problem(f"is not valid JSON (line {exc.lineno}, column {exc.colno})")

    if not isinstance(document, dict):
        return file_problem("does not hold a JSON object")
    raw_servers = document.get("mcpServers")
    if raw_servers is None:
        return ClientServers(client)
    if not isinstance(raw_servers, dict):
        return file_problem("`mcpServers` is not an object")

    servers: list[ClientServer] = []
    problems: list[EntryProblem] = []
    for name, raw in raw_servers.items():
        if name == HANGAR_ENTRY_NAME:
            continue
        parsed = _parse_entry(name, raw)
        if isinstance(parsed, str):
            problems.append(EntryProblem(client.key, client.path, name, parsed))
        else:
            servers.append(parsed)
    return ClientServers(client, tuple(servers), tuple(problems))


def _parse_entry(name: str, raw: object) -> ClientServer | str:
    """One entry as a server, or the reason it is not one. A reason never quotes a value."""
    if not isinstance(raw, dict):
        return "is not an object"

    declared = raw.get("type")
    if declared is not None and not isinstance(declared, str):
        return "`type` is not a string"
    has_command = "command" in raw
    has_url = "url" in raw
    if has_command and has_url:
        return "has both `command` and `url`"
    if not has_command and not has_url:
        return "has neither `command` nor `url`"

    if has_command:
        if declared not in (None, "stdio"):
            return f"declares type {declared!r} but has a `command`"
        command = raw["command"]
        if not isinstance(command, str) or not command.strip():
            return "`command` is not a non-empty string"
        args = raw.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            return "`args` is not a list of strings"
        env = _string_map(raw.get("env", {}))
        if env is None:
            return "`env` is not an object of string values"
        return StdioServer(name, command, tuple(args), env)

    if declared is not None and declared not in _REMOTE_TYPES:
        return f"declares type {declared!r} but has a `url`"
    url = raw["url"]
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return "`url` is not an http(s) URL"
    headers = _string_map(raw.get("headers", {}))
    if headers is None:
        return "`headers` is not an object of string values"
    return RemoteServer(name, url, _REMOTE_TYPES[declared] if declared else None, headers)


def _string_map(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        return None
    return dict(value)
