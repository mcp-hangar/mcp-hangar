"""Tier 0 live verification: a client that gives up on a held call cancels its approval (#2279).

A call held for approval on stdio is the client's request. When the client
stops waiting -- its own request timeout, which the SDK follows with
``notifications/cancelled`` -- the gateway used to end only the request: the
batch under it went on holding, the record stayed ``pending``, and a later
``mcp-hangar approvals approve`` exited 0 and ran a call the client had been
told failed. Now the cancel reaches the hold, the record turns ``cancelled``
at once, the CLI is refused, and the upstream never sees the call.

A call a human approved before the client left is not affected: it runs, once.

Over streamable HTTP the cancel takes another way to the hold; see
``test_t0_http_client_cancel_withdraws_hold.py`` (#2289).

BLACK-BOX against a real ``mcp-hangar serve`` over stdio. The upstream appends
a line to a file each time a tool runs, so "never ran" and "ran once" are read
from the upstream itself. The approver is the CLI in another process. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_stdio_client_cancel_withdraws_hold.py \\
        -m "live and t0" -o addopts="" -n auto --timeout=180
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession

from tests._hangar_executable import hangar_executable

pytestmark = [pytest.mark.live, pytest.mark.t0]

#: How long the client waits for the held call before it gives up.
_CLIENT_GIVES_UP_S = 6.0
#: How long the slow tool runs: past the client's patience, so the client leaves mid-call.
_SLOW_TOOL_S = 8.0

_UPSTREAM = """\
import sys
import time

from mcp.server.mcpserver import MCPServer

mcp = MCPServer(name="counted", version="0.1.0")


def _ran(text: str) -> None:
    with open(sys.argv[1], "a") as runs:
        runs.write(text + "\\n")


@mcp.tool(description="Send a message.")
def send_message(text: str) -> str:
    _ran(text)
    return text


@mcp.tool(description="Send a message, slowly.")
def send_slowly(text: str) -> str:
    _ran(text)
    time.sleep({slow})
    return text


if __name__ == "__main__":
    mcp.run()
"""

_CONFIG = """\
logging:
  level: WARNING
mcp_servers:
  demo:
    mode: subprocess
    command: ["{python}", "{upstream}", "{runs}"]
    idle_ttl_s: 120
    tools:
      approval_list: [send_message, send_slowly]
      approval_timeout_seconds: 60
{projection}auth:
  stdio:
    principal:
      id: local-user
      tenant_id: local
      roles: [provider-admin]
event_store:
  driver: sqlite
  path: "{state}/events.db"
"""

_FRONT_DOOR = """\
    tool_projection:
      flat: [send_message, send_slowly]
tool_access:
  mode: front_door
"""


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([hangar_executable(), *args], capture_output=True, text=True, timeout=30)


def _rows(config: Path, state: str) -> list[dict[str, Any]]:
    listed = _cli("--json", "approvals", "list", "--state", state, "--config", str(config))
    assert listed.returncode == 0, listed.stdout + listed.stderr
    rows: list[dict[str, Any]] = json.loads(listed.stdout.strip().splitlines()[-1])
    return rows


def _write_config(tmp_path: Path, *, front_door: bool) -> tuple[Path, Path]:
    upstream = tmp_path / "counted.py"
    upstream.write_text(_UPSTREAM.replace("{slow}", repr(_SLOW_TOOL_S)))
    runs = tmp_path / "runs.txt"
    config = tmp_path / "config.yaml"
    config.write_text(
        _CONFIG.format(
            python=sys.executable,
            upstream=upstream,
            runs=runs,
            state=tmp_path,
            projection=_FRONT_DOOR if front_door else "",
        )
    )
    return config, runs


def _ran(runs: Path) -> list[str]:
    return runs.read_text().splitlines() if runs.exists() else []


async def _held(config: Path) -> str:
    """The id of the call once it is held."""
    for _ in range(60):
        rows = await asyncio.to_thread(_rows, config, "pending")
        if rows:
            return str(rows[0]["approval_id"])
        await asyncio.sleep(0.25)
    raise AssertionError("the call was never held")


async def _call(
    session: ClientSession, front_door: bool, tool: str, text: str, patience: float | None = _CLIENT_GIVES_UP_S
) -> None:
    """Make the call and give up on it after *patience*, as a client with a request timeout does."""
    if front_door:
        await session.call_tool(tool, {"text": text}, read_timeout_seconds=patience)
    else:
        calls = [{"mcp_server": "demo", "tool": tool, "arguments": {"text": text}}]
        await session.call_tool("hangar_call", {"calls": calls}, read_timeout_seconds=patience)


async def _give_up_then_approve(
    session: ClientSession, config: Path, *, front_door: bool, connect: bool = True
) -> dict[str, Any]:
    """Hold a call, let the client give up on it, then watch the record and approve it from the CLI.

    *connect* runs the handshake first; a caller that already negotiated another way passes False.
    """
    if connect:
        await session.initialize()
    held = asyncio.create_task(_held(config))
    with pytest.raises(Exception):  # noqa: B017 -- the SDK's timeout error type is not part of the contract
        await _call(session, front_door, "send_message", "the agent was told this failed")
    gave_up = time.monotonic()
    approval_id = await held
    # The record leaves `pending` without anyone deciding.
    cancelled: list[dict[str, Any]] = []
    while time.monotonic() - gave_up < 10:
        cancelled = await asyncio.to_thread(_rows, config, "cancelled")
        if cancelled:
            break
        await asyncio.sleep(0.1)
    seen_cancelled_after = time.monotonic() - gave_up
    approved = await asyncio.to_thread(_cli, "approvals", "approve", approval_id, "--config", str(config))
    # Long enough for a call the approval released to reach the upstream.
    await asyncio.sleep(3)
    return {
        "approval_id": approval_id,
        "cancelled": cancelled,
        "seen_cancelled_after": seen_cancelled_after,
        "approved": approved,
        "pending": await asyncio.to_thread(_rows, config, "pending"),
    }


def _abandon_a_held_call(config: Path, *, front_door: bool) -> dict[str, Any]:
    from tests.live._mcp_client import open_stdio_streams

    async def run() -> dict[str, Any]:
        async with open_stdio_streams(hangar_executable(), ["--config", str(config), "serve"], {**os.environ}) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                return await _give_up_then_approve(session, config, front_door=front_door)

    return asyncio.run(asyncio.wait_for(run(), timeout=120))


def _assert_withdrawn(outcome: dict[str, Any], runs: Path) -> None:
    said = " ".join((outcome["approved"].stdout + outcome["approved"].stderr).split())
    assert [r["approval_id"] for r in outcome["cancelled"]] == [outcome["approval_id"]], outcome["cancelled"]
    # Within a second of the cancel reaching the gateway; the CLI reads the
    # store in a process of its own each time, so the bound is looser here.
    assert outcome["seen_cancelled_after"] < 5, outcome["seen_cancelled_after"]
    assert outcome["approved"].returncode == 1, said
    assert "will not run" in said, said
    assert "approved by" not in said, said
    assert outcome["pending"] == []
    assert _ran(runs) == [], _ran(runs)


def test_a_front_door_call_the_client_gave_up_on_is_cancelled_and_never_runs(tmp_path: Path) -> None:
    config, runs = _write_config(tmp_path, front_door=True)

    _assert_withdrawn(_abandon_a_held_call(config, front_door=True), runs)


def test_a_hangar_call_the_client_gave_up_on_is_cancelled_and_never_runs(tmp_path: Path) -> None:
    config, runs = _write_config(tmp_path, front_door=False)

    _assert_withdrawn(_abandon_a_held_call(config, front_door=False), runs)


@pytest.mark.parametrize("front_door", [True, False], ids=["front_door", "hangar_call"])
def test_a_call_approved_before_the_client_gave_up_runs_once(tmp_path: Path, front_door: bool) -> None:
    """Approved first; the client then gives up while the upstream is still running the call.

    The client gives up by cancelling its request once the approval is recorded
    and the upstream has started, not on a timer: under load the approver's
    CLI can take longer than any fixed patience, and an approval after the
    client left is refused, as it should be.
    """
    from tests.live._mcp_client import open_stdio_streams

    config, runs = _write_config(tmp_path, front_door=front_door)

    async def run() -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
        async with open_stdio_streams(hangar_executable(), ["--config", str(config), "serve"], {**os.environ}) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def approve() -> subprocess.CompletedProcess[str]:
                    approval_id = await _held(config)
                    return await asyncio.to_thread(_cli, "approvals", "approve", approval_id, "--config", str(config))

                call = asyncio.create_task(_call(session, front_door, "send_slowly", "approved in time", None))
                approved = await approve()
                for _ in range(200):
                    if _ran(runs) or call.done():
                        break
                    await asyncio.sleep(0.1)
                call.cancel()  # the client leaves while the upstream runs the granted call
                with contextlib.suppress(asyncio.CancelledError):
                    await call
                # Past the slow tool's end, so a second run would have shown.
                await asyncio.sleep(_SLOW_TOOL_S)
                return approved, await asyncio.to_thread(_rows, config, "approved")

    approved, approved_rows = asyncio.run(asyncio.wait_for(run(), timeout=120))

    said = " ".join((approved.stdout + approved.stderr).split())
    assert approved.returncode == 0, said
    assert "approved by local-user" in said, said
    assert len(approved_rows) == 1, approved_rows
    assert _ran(runs) == ["approved in time"], _ran(runs)
