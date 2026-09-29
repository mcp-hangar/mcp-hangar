"""Tier 0 live verification: over stdio with auth on, ``tool:invoke`` follows the declared caller (#1622).

A stdio session carries no request, so ADR-026's ``auth.stdio.principal`` names
its caller. With ``auth.enabled: true`` both invoke paths check ``tool:invoke``,
and they check it for that principal on its declared roles: a ``developer`` is
served and a ``viewer`` is refused. Before #1622, ``hangar_call`` refused every
stdio caller as anonymous, and the front door's flat call checked nothing.

The gateway is the shipped console script over stdio, driven by the SDK's own
``ClientSession``: the flat call on a front door, ``hangar_call`` on egress.
Skip-safe without the stub backend. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_stdio_tool_invoke.py -m "live and t0" -o addopts=""
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession

from tests._hangar_executable import hangar_executable
from tests.live.conftest import _MATH_SERVER

pytestmark = [pytest.mark.live, pytest.mark.t0]

_CONFIG = """\
logging:
  level: WARNING
tool_access:
  mode: {topology}
auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
  storage:
    driver: memory
  stdio:
    principal:
      id: local-user
      tenant_id: local
      roles: [{role}]
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
"""


def _call(workdir: Path, topology: str, role: str) -> tuple[bool, str]:
    """Call ``add`` once through a stdio gateway: (refused, what the caller read)."""
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    config = workdir / "config.yaml"
    config.write_text(_CONFIG.format(topology=topology, role=role, python=sys.executable, server=str(_MATH_SERVER)))
    from tests.live._mcp_client import open_stdio_streams

    async def _run() -> tuple[bool, str]:
        args = ["--config", str(config), "serve"]
        async with open_stdio_streams(hangar_executable(), args, {**os.environ}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                if topology == "front_door":
                    # The front door warms its servers at boot; wait for the tool.
                    for _ in range(30):
                        if "add" in {t.name for t in (await session.list_tools()).tools}:
                            break
                        await asyncio.sleep(0.5)
                    result = await session.call_tool("add", {"a": 2, "b": 3})
                    dumped: Any = result.model_dump(mode="json")
                    text = " ".join(block.get("text", "") for block in dumped.get("content") or [])
                    return bool(dumped.get("isError") or dumped.get("is_error")), text
                calls = [{"mcp_server": "math", "tool": "add", "arguments": {"a": 2, "b": 3}}]
                result = await session.call_tool("hangar_call", {"calls": calls})
                batch = json.loads(result.content[0].text)
                [outcome] = batch["results"]
                return not outcome["success"], outcome.get("error") or json.dumps(outcome.get("result"))

    return asyncio.run(asyncio.wait_for(_run(), timeout=90))


@pytest.mark.parametrize("topology", ["front_door", "egress"], ids=["flat-call", "hangar_call"])
def test_a_declared_developer_is_served(tmp_path: Path, topology: str) -> None:
    refused, text = _call(tmp_path, topology, "developer")

    assert not refused, text
    assert "5" in text, text


@pytest.mark.parametrize("topology", ["front_door", "egress"], ids=["flat-call", "hangar_call"])
def test_a_declared_viewer_is_refused(tmp_path: Path, topology: str) -> None:
    refused, text = _call(tmp_path, topology, "viewer")

    assert refused, text
    assert text == "Not authorized to invoke tool 'add': tool:invoke permission required", text
