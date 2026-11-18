"""Tier 0 live verification: a front-door call held past 30 s and approved inside its window runs once (#2271).

A front door gives every flat ``tools/call`` a 30 s budget, and the approval
hold used to run inside it: a human who approved after 30 s saw
``mcp-hangar approvals approve`` succeed, and the call was refused
``CancellationError`` and never ran. The hold is now outside the budget.

BLACK-BOX against a real ``mcp-hangar serve``, over stdio and over
streamable HTTP. The upstream is a one-tool server that appends a line to a
file each time its tool runs, so "runs exactly once" is read from the upstream
itself. The approver is the CLI in another process, as a user runs it, and the
gateway reads the decision from the shared approval store.

Each test holds a call for more than 30 s, so the tier runs them in parallel
(``-n auto``). Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_approval_hold_outside_call_budget.py \\
        -m "live and t0" -o addopts="" -n auto --timeout=180
"""

from __future__ import annotations

import asyncio
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
from tests.live import _group_support as gs
from tests.live.conftest import running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

#: Past the front door's 30 s call budget, counted from when the call is held.
_APPROVE_AFTER_S = 33.0
_TENANT = "tenant-hold"

_COUNTED_UPSTREAM = """\
import sys

from mcp.server.mcpserver import MCPServer

mcp = MCPServer(name="counted", version="0.1.0")


@mcp.tool(description="Echo the text back.")
def echo(text: str) -> str:
    with open(sys.argv[1], "a") as runs:
        runs.write(text + "\\n")
    return text


if __name__ == "__main__":
    mcp.run()
"""

_SERVER = """\
mcp_servers:
  demo:
    mode: subprocess
    command: ["{python}", "{upstream}", "{runs}"]
    idle_ttl_s: 120
    tools:
      approval_list: [echo]
      approval_timeout_seconds: {window}
    tool_projection:
      flat: [echo]
tool_access:
  mode: front_door
event_store:
  driver: sqlite
  path: "{state}/events.db"
"""

_STDIO = """\
logging:
  level: WARNING
{approvals}auth:
  stdio:
    principal:
      id: local-user
      tenant_id: local
      roles: [provider-admin]
"""

_HTTP = """\
logging:
  level: WARNING
auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: "{auth_db}"
  role_assignments:
    - principal: "svc:{tenant}"
      role: developer
      scope: global
  stdio:
    principal:
      id: local-approver
      tenant_id: {tenant}
      roles: [provider-admin]
"""


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([hangar_executable(), *args], capture_output=True, text=True, timeout=30)


def _rows(config: Path, state: str = "pending") -> list[dict[str, Any]]:
    listed = _cli("--json", "approvals", "list", "--state", state, "--config", str(config))
    return json.loads(listed.stdout.strip().splitlines()[-1]) if listed.returncode == 0 and listed.stdout else []


def _write_config(tmp_path: Path, head: str, window: int) -> tuple[Path, Path]:
    upstream = tmp_path / "counted.py"
    upstream.write_text(_COUNTED_UPSTREAM)
    runs = tmp_path / "runs.txt"
    config = tmp_path / "config.yaml"
    config.write_text(
        head + _SERVER.format(python=sys.executable, upstream=upstream, runs=runs, window=window, state=tmp_path)
    )
    return config, runs


async def _approve_late(config: Path) -> str:
    """Approve the held call `_APPROVE_AFTER_S` after it is first seen held."""
    for _ in range(120):
        rows = await asyncio.to_thread(_rows, config)
        if rows:
            await asyncio.sleep(_APPROVE_AFTER_S)
            approved = await asyncio.to_thread(
                _cli, "approvals", "approve", rows[0]["approval_id"], "--config", str(config)
            )
            return approved.stdout + approved.stderr
        await asyncio.sleep(0.5)
    raise AssertionError("the call was never held")


async def _call_echo(session: ClientSession, text: str) -> tuple[bool, str]:
    await session.initialize()
    for _ in range(30):
        if "echo" in {t.name for t in (await session.list_tools()).tools}:
            break
        await asyncio.sleep(0.5)
    result = (await session.call_tool("echo", {"text": text})).model_dump(mode="json")
    failed = bool(result.get("isError") or result.get("is_error"))
    return failed, " ".join(b.get("text", "") for b in result.get("content") or [])


def _over_stdio(config: Path, text: str, approve: bool) -> tuple[bool, str, str, float]:
    from tests.live._mcp_client import open_stdio_streams

    async def run() -> tuple[bool, str, str, float]:
        async with open_stdio_streams(hangar_executable(), ["--config", str(config), "serve"], {**os.environ}) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                approver = asyncio.create_task(_approve_late(config)) if approve else None
                started = time.monotonic()
                failed, said = await _call_echo(session, text)
                took = time.monotonic() - started
                return failed, said, (await approver) if approver else "", took

    return asyncio.run(asyncio.wait_for(run(), timeout=150))


def test_a_stdio_call_approved_after_30_s_runs_once(tmp_path: Path) -> None:
    """The stdio defaults: `stdio_wait_seconds` 50, so the window outlasts the 30 s budget."""
    config, runs = _write_config(tmp_path, _STDIO.format(approvals=""), window=60)

    failed, text, said, took = _over_stdio(config, "released", approve=True)

    assert "approved by local-user" in said, said
    assert not failed, text
    assert text == "released", text
    assert took > 30, took
    assert runs.read_text().splitlines() == ["released"]


def test_a_stdio_call_nobody_approves_is_refused_when_its_window_ends(tmp_path: Path) -> None:
    approvals = "approvals:\n  stdio_wait_seconds: 33\n"
    config, runs = _write_config(tmp_path, _STDIO.format(approvals=approvals), window=60)

    failed, text, _, took = _over_stdio(config, "never", approve=False)

    assert failed, text
    assert took > 33, took
    assert not runs.exists() or not runs.read_text(), runs.read_text()
    assert [r["state"] for r in _rows(config, "expired")] == ["expired"]


def test_an_http_front_door_call_approved_after_30_s_runs_once(tmp_path: Path) -> None:
    """Off stdio no wait limit applies: the window is `approval_timeout_seconds`."""
    from tests.live._mcp_client import open_mcp_streams

    auth_db = tmp_path / "auth.db"
    try:
        keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")
    config, runs = _write_config(tmp_path, _HTTP.format(auth_db=auth_db, tenant=_TENANT), window=60)

    with running_hangar(tmp_path, config.read_text()) as hangar:

        async def run() -> tuple[bool, str, str, float]:
            async with open_mcp_streams(f"{hangar.base_url}/mcp", {"X-API-Key": keys[_TENANT]}) as (read, write):
                async with ClientSession(read, write) as session:
                    approver = asyncio.create_task(_approve_late(config))
                    started = time.monotonic()
                    failed, said = await _call_echo(session, "released over http")
                    return failed, said, await approver, time.monotonic() - started

        failed, text, said, took = asyncio.run(asyncio.wait_for(run(), timeout=150))

    assert "approved by local-approver" in said, said
    assert not failed, f"{text}\n{hangar.output()}"
    assert text == "released over http", text
    assert took > 30, took
    assert runs.read_text().splitlines() == ["released over http"]
