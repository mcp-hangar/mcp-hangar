"""Tier 0 live verification: `mcp-hangar approvals approve` refuses a hold the gateway abandoned (#2272).

The client spawns ``mcp-hangar serve`` over stdio and sends one ``hangar_call``
batch with ``fail_fast``: ``echo``, which is on ``approval_list`` and is held,
and a call to a server that dies about 10 s into its start. That failure cancels the batch
while ``echo`` is held, so the gateway abandons the hold. The CLI, in another
process, cannot see the gateway's memory; it reads the shared approval store.

Before #2272 the record stayed ``pending`` until the approval window ended, and
the CLI printed ``approved by ...`` and exited 0 for a call that never ran. Now
the gateway moves the record to ``cancelled`` when it abandons the hold, so
``approvals list`` stops showing it and ``approvals approve`` exits non-zero
saying the call will not run.

The abandonment comes from ``fail_fast``, not from a call budget, so it does
not depend on how long a held call may wait. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_stdio_abandoned_hold_cli.py -m "live and t0" -o addopts=""
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession

from tests._hangar_executable import hangar_executable

pytestmark = [pytest.mark.live, pytest.mark.t0]

_UPSTREAM = Path(__file__).resolve().parents[2] / "examples" / "rugpull" / "server.py"

_CONFIG = """\
logging:
  level: WARNING
mcp_servers:
  demo:
    mode: subprocess
    command: ["{python}", "{server}"]
    idle_ttl_s: 60
    tools:
      approval_list: [echo]
      approval_timeout_seconds: 60
  dies_starting:
    mode: subprocess
    command: ["{python}", "{dies}"]
auth:
  stdio:
    principal:
      id: local-user
      tenant_id: local
      roles: [provider-admin]
event_store:
  driver: sqlite
  path: "{state}/events.db"
"""


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([hangar_executable(), *args], capture_output=True, text=True, timeout=30)


def _listed(config: Path, state: str = "pending") -> list[dict[str, Any]]:
    listed = _cli("--json", "approvals", "list", "--state", state, "--config", str(config))
    assert listed.returncode == 0, listed.stdout + listed.stderr
    rows: list[dict[str, Any]] = json.loads(listed.stdout.strip().splitlines()[-1])
    return rows


def test_approving_an_abandoned_hold_from_the_cli_is_refused(tmp_path: Path) -> None:
    from tests.live._mcp_client import open_stdio_streams

    config = tmp_path / "config.yaml"
    dies = tmp_path / "dies_starting.py"
    dies.write_text("import time\n\ntime.sleep(10)\n")  # exits without a handshake
    config.write_text(_CONFIG.format(python=sys.executable, server=_UPSTREAM, dies=dies, state=tmp_path))

    async def approve_once_abandoned() -> tuple[str, subprocess.CompletedProcess[str], list[dict[str, Any]]]:
        for _ in range(40):
            rows = await asyncio.to_thread(_listed, config)
            if rows:
                break
            await asyncio.sleep(0.5)
        else:
            raise AssertionError("the call was never held")
        approval_id = rows[0]["approval_id"]
        # The sibling dies about 10 s in; give the abandonment time to reach the store.
        for _ in range(60):
            if not await asyncio.to_thread(_listed, config):
                break
            await asyncio.sleep(0.5)
        approved = await asyncio.to_thread(_cli, "approvals", "approve", approval_id, "--config", str(config))
        cancelled = await asyncio.to_thread(_listed, config, "cancelled")
        return approval_id, approved, cancelled

    async def run() -> tuple[dict[str, Any], tuple[str, subprocess.CompletedProcess[str], list[dict[str, Any]]]]:
        async with open_stdio_streams(hangar_executable(), ["--config", str(config), "serve"], {**os.environ}) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                approver = asyncio.create_task(approve_once_abandoned())
                calls = [
                    {"mcp_server": "demo", "tool": "echo", "arguments": {"text": "never sent"}},
                    {"mcp_server": "dies_starting", "tool": "anything", "arguments": {}},
                ]
                result = await session.call_tool(
                    "hangar_call", {"calls": calls, "fail_fast": True, "max_concurrency": 2}
                )
                said = await approver
                return json.loads(result.content[0].text), said

    batch, (approval_id, approved, cancelled) = asyncio.run(asyncio.wait_for(run(), timeout=150))

    said = " ".join((approved.stdout + approved.stderr).split())  # the console wraps at its width
    assert approved.returncode == 1, said
    assert "will not run" in said, said
    assert "approved by" not in said, said
    assert [r["approval_id"] for r in cancelled] == [approval_id], cancelled
    assert _listed(config) == []
    echo = next(r for r in batch["results"] if r["index"] == 0)
    assert not echo["success"], echo
    assert echo["error_type"] == "CancellationError", echo
