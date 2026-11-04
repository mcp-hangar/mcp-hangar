#!/usr/bin/env python3
"""Run the README quickstart as a reader would, and play their client (#1774, #2182).

A fenced ``bash`` block that follows a ``<!-- ci:quickstart -->`` marker line is
part of the quickstart. They are joined into one script and run with ``bash -e``
in an empty directory under a throwaway ``HOME`` -- one shell, as a reader has
it, so a PATH exported by the first block is still there for the second -- so a
step that only works on the author's machine -- a binary that is on the author's
PATH, a file left from last week -- fails here instead of in front of a reader.

The reader is not a blank machine: they have MCP servers of their own. Before
the first block the directory gets a ``.mcp.json`` with two fixture servers, and
the quickstart has to move them behind Hangar together with the demo.

Where the README says to restart the client and look, a ``<!-- ci:check NAME -->``
line stands in for the reader: this script, run again with the installer's
Python (which has the ``mcp`` SDK), spawns the ``mcp-hangar`` entry ``init``
wrote, exactly as a client would, and checks what a client sees:

- ``served``: the client file lists only Hangar, and every server's tools are
  served through it, ``echo`` working;
- ``withheld``: ``echo`` drifted from its pin, so it is gone from ``tools/list``,
  a call of it is answered as a call of a name nothing serves (``-32601``,
  ADR-029, #905), ``hangar_details`` names it ``digest_drift``, and
  ``mcp-hangar pin --check`` exits 1. The time from the first block to here is
  printed: it is what the README states.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

MARKER = "<!-- ci:quickstart -->"
CHECK = re.compile(r"^<!-- ci:check ([a-z]+) -->$")
FENCE = re.compile(r"^```bash\n(.*?)^```$", re.DOTALL | re.MULTILINE)

#: The servers the reader already has, one tool each, named apart from the demo's `echo`.
FIXTURE_TOOLS = {"notes": "add_note", "calendar": "next_meeting"}
FIXTURE_SERVER = """\
import os
from mcp.server.mcpserver import MCPServer

TOOL = os.environ["FIXTURE_TOOL"]
mcp = MCPServer(name=TOOL, version="0.1.0")


@mcp.tool(name=TOOL, description=f"A fixture tool, {TOOL}.")
def tool(text: str) -> str:
    return text


if __name__ == "__main__":
    mcp.run()
"""
DRIFTED = "echo"
STARTED_ENV = "README_QUICKSTART_STARTED"


def quickstart_steps(text: str) -> list[tuple[str, str]]:
    """The marked blocks and check lines, in reading order: ("run", script) or ("check", name)."""
    steps: list[tuple[int, str, str]] = []
    for match in FENCE.finditer(text):
        before = text[: match.start()].rstrip("\n").rsplit("\n", 1)[-1]
        if before.strip() == MARKER:
            steps.append((match.start(), "run", match.group(1)))
    offset = 0
    for line in text.splitlines(keepends=True):
        check = CHECK.match(line.strip())
        if check:
            steps.append((offset, "check", check.group(1)))
        offset += len(line)
    return [(kind, body) for _, kind, body in sorted(steps)]


def quickstart_blocks(text: str) -> list[str]:
    return [body for kind, body in quickstart_steps(text) if kind == "run"]


def seed_fixture(work: Path, home: Path) -> None:
    """The reader's own servers, in the project's `.mcp.json`, run by the Python the installer brings."""
    server = work / "fixture_server.py"
    server.write_text(FIXTURE_SERVER, encoding="utf-8")
    python = str(home / ".mcp-hangar" / "venv" / "bin" / "python")
    servers = {
        name: {"type": "stdio", "command": python, "args": [str(server)], "env": {"FIXTURE_TOOL": tool}}
        for name, tool in FIXTURE_TOOLS.items()
    }
    (work / ".mcp.json").write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n", encoding="utf-8")


def script_for(steps: list[tuple[str, str]], work: Path) -> str:
    client = f'"$HOME/.mcp-hangar/venv/bin/python" -I {shlex.quote(str(Path(__file__).resolve()))}'
    parts = []
    for kind, body in steps:
        if kind == "run":
            parts.append(body)
        else:
            parts.append(f"{client} --check {shlex.quote(body)} {shlex.quote(str(work))}\n")
    return "\n".join(parts)


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "--check":
        return check(argv[2], Path(argv[3]))
    readme = Path(argv[1] if len(argv) > 1 else "README.md").resolve()
    steps = quickstart_steps(readme.read_text(encoding="utf-8"))
    if not any(kind == "run" for kind, _ in steps):
        print(f"no {MARKER} block in {readme}: nothing to run", file=sys.stderr)
        return 1
    checks = [body for kind, body in steps if kind == "check"]
    if checks != ["served", "withheld"]:
        print(f"expected the checks served, withheld in that order; the README has {checks}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as work:
        seed_fixture(Path(work), Path(home))
        env = {**os.environ, "HOME": home, STARTED_ENV: str(time.monotonic())}
        script = script_for(steps, Path(work))
        print(script, flush=True)
        result = subprocess.run(["bash", "-e", "-c", script], cwd=work, env=env, check=False)
        if result.returncode:
            print(f"the quickstart exited {result.returncode}", file=sys.stderr)
            return result.returncode
    return 0


# --- the client, run with the installer's Python -----------------------------------------


def check(name: str, work: Path) -> int:
    import anyio

    problems = anyio.run(_CHECKS[name], work)
    for problem in problems:
        print(f"check {name}: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"check {name}: ok", flush=True)
    if name == "withheld":
        elapsed = time.monotonic() - float(os.environ[STARTED_ENV])
        print(f"from install to the withheld tool: {elapsed:.0f} s", flush=True)
    return 0


def _client_entry(work: Path) -> tuple[dict[str, Any], list[str]]:
    servers = json.loads((work / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    left = sorted(set(servers) - {"mcp-hangar"})
    return servers["mcp-hangar"], [f"{name} is still in the client, not behind Hangar" for name in left]


async def _session(entry: dict[str, Any], body):  # noqa: ANN001 -- a coroutine taking the session
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=entry["command"], args=entry["args"], env=dict(os.environ))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        return await body(session)


def _text(result: Any) -> str:
    return " ".join(getattr(item, "text", "") for item in result.content)


def _is_error(result: Any) -> bool:
    return bool(getattr(result, "is_error", getattr(result, "isError", False)))


async def _error_of(session: Any, tool: str) -> tuple[Any, str] | None:
    """The JSON-RPC error a call of *tool* gets, or None when it is answered."""
    try:
        await session.call_tool(tool, {"text": "hi"})
    except Exception as exc:  # noqa: BLE001 -- the SDK's error type moved between versions
        error = getattr(exc, "error", exc)
        return getattr(error, "code", None), str(getattr(error, "message", exc))
    return None


async def _served(work: Path) -> list[str]:
    entry, problems = _client_entry(work)

    async def body(session: Any) -> list[str]:
        found = []
        listed = {tool.name for tool in (await session.list_tools()).tools}
        for tool in [DRIFTED, *FIXTURE_TOOLS.values()]:
            if tool not in listed:
                found.append(f"{tool} is not in tools/list: {sorted(listed)}")
                continue
            result = await session.call_tool(tool, {"text": "hi"})
            if _is_error(result) or "hi" not in _text(result):
                found.append(f"calling {tool} did not answer hi: {_text(result)}")
        return found

    return problems + await _session(entry, body)


async def _withheld(work: Path) -> list[str]:
    entry, problems = _client_entry(work)
    unknown = "no_such_tool_anywhere"

    async def body(session: Any) -> list[str]:
        found = []
        listed = {tool.name for tool in (await session.list_tools()).tools}
        if DRIFTED in listed:
            found.append(f"{DRIFTED} drifted from its pin and is still in tools/list")
        for tool in FIXTURE_TOOLS.values():
            if tool not in listed:
                found.append(f"{tool} did not drift and is gone from tools/list")
        drifted, absent = await _error_of(session, DRIFTED), await _error_of(session, unknown)
        if drifted is None or absent is None or drifted[0] != -32601:
            found.append(f"calling {DRIFTED} got {drifted}, a name nothing serves got {absent}; both should be -32601")
        elif (drifted[0], drifted[1].replace(DRIFTED, unknown)) != absent:
            found.append(f"calling {DRIFTED} got {drifted}, which tells it apart from a name nothing serves: {absent}")
        details = json.loads(_text(await session.call_tool("hangar_details", {"mcp_server": "demo"})))
        withheld = {item["name"]: item["reason"] for item in details.get("withheld_tools", [])}
        if withheld.get(DRIFTED) != "digest_drift":
            found.append(f"hangar_details demo does not name {DRIFTED} as digest_drift: {withheld}")
        return found

    problems += await _session(entry, body)
    config = entry["args"][entry["args"].index("--config") + 1]
    pin = subprocess.run([entry["command"], "--config", config, "pin", "--check"], capture_output=True, check=False)
    if pin.returncode != 1:
        problems.append(f"mcp-hangar pin --check exited {pin.returncode}, not 1")
    return problems


_CHECKS = {"served": _served, "withheld": _withheld}


if __name__ == "__main__":
    sys.exit(main(sys.argv))
