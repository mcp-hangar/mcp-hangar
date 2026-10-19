"""Tier 0 live verification: a call held over stdio is released by `mcp-hangar approvals approve` (#1778).

The README-style demo, as a user runs it: the client spawns ``mcp-hangar serve``
over stdio with ``approval_list: [echo]``, the call to ``echo`` is held, and
``mcp-hangar approvals approve`` in another process -- another terminal --
releases it. The gateway sees the decision by polling the shared approval store;
there is no listener. The call returns the upstream's own result.

The default suite carries the same proof in
``tests/integration/test_a_held_call_is_resolved_from_the_cli.py``; this is the
opt-in tier's copy, driven through the live harness's SDK stdio client. Run
with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_stdio_approval_cli.py -m "live and t0" -o addopts=""
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

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
tool_access:
  mode: front_door
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


def test_a_held_call_is_released_by_the_cli(tmp_path: Path) -> None:
    from tests.live._mcp_client import open_stdio_streams

    config = tmp_path / "config.yaml"
    config.write_text(_CONFIG.format(python=sys.executable, server=_UPSTREAM, state=tmp_path))

    async def approve_when_held() -> str:
        for _ in range(120):
            listed = await asyncio.to_thread(_cli, "--json", "approvals", "list", "--config", str(config))
            rows = json.loads(listed.stdout.strip().splitlines()[-1]) if listed.returncode == 0 else []
            if rows:
                approved = await asyncio.to_thread(
                    _cli, "approvals", "approve", rows[0]["approval_id"], "--config", str(config)
                )
                return approved.stdout + approved.stderr
            await asyncio.sleep(0.5)
        raise AssertionError("the call was never held")

    async def run() -> tuple[str, str]:
        async with open_stdio_streams(hangar_executable(), ["--config", str(config), "serve"], {**os.environ}) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for _ in range(30):
                    if "echo" in {t.name for t in (await session.list_tools()).tools}:
                        break
                    await asyncio.sleep(0.5)
                approver = asyncio.create_task(approve_when_held())
                result = await session.call_tool("echo", {"text": "released"})
                said = await approver
                dumped = result.model_dump(mode="json")
                assert not (dumped.get("isError") or dumped.get("is_error")), dumped
                return " ".join(b.get("text", "") for b in dumped.get("content") or []), said

    text, said = asyncio.run(asyncio.wait_for(run(), timeout=90))

    assert "approved by local-user" in said, said
    assert text == "released", text
