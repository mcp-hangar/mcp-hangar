"""Tier 0 live verification: over stdio with auth on, the declared caller may call what it is listed (#1627).

A stdio session carries no request, so ADR-026's ``auth.stdio.principal`` names
its caller. The front door lists the ``hangar_*`` tools that principal's
declared roles permit, and ``authorize_tool`` now decides them for it on the
same roles. Before #1627 it read the principal only from the request, so with
``auth.enabled: true`` every listed management tool was refused as
unauthenticated.

Two topologies, because each proves something different:

* front door: the management surface is projected per caller, so a declared
  ``admin`` is listed ``hangar_status`` and ``hangar_warm`` and is served both.
  (A tool the front door does not list is ``-32601`` before any authorization,
  so a refusal cannot be shown there.)
* egress: every ``hangar_*`` tool is registered, so the call reaches
  ``authorize_tool`` whoever asks: a declared ``admin`` is served ``hangar_warm``
  and a declared ``viewer`` is refused it.

The gateway is the shipped console script over stdio, driven by the SDK's own
``ClientSession``. Skip-safe without the stub backend. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_stdio_management_tools.py -m "live and t0" -o addopts=""
"""

from __future__ import annotations

import asyncio
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


def _session(workdir: Path, topology: str, role: str, tools: list[str]) -> tuple[set[str], dict[str, tuple[bool, str]]]:
    """List, then call each of *tools* once: (listed names, name -> (refused, text))."""
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    config = workdir / "config.yaml"
    config.write_text(_CONFIG.format(topology=topology, role=role, python=sys.executable, server=str(_MATH_SERVER)))
    from tests.live._mcp_client import open_stdio_streams

    async def _run() -> tuple[set[str], dict[str, tuple[bool, str]]]:
        args = ["--config", str(config), "serve"]
        async with open_stdio_streams(hangar_executable(), args, {**os.environ}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = {t.name for t in (await session.list_tools()).tools}
                outcomes: dict[str, tuple[bool, str]] = {}
                for name in tools:
                    arguments = {"mcp_servers": "math"} if name == "hangar_warm" else {}
                    dumped: Any = (await session.call_tool(name, arguments)).model_dump(mode="json")
                    text = " ".join(block.get("text", "") for block in dumped.get("content") or [])
                    outcomes[name] = (bool(dumped.get("isError") or dumped.get("is_error")), text)
                return listed, outcomes

    return asyncio.run(asyncio.wait_for(_run(), timeout=90))


def test_a_declared_admin_on_a_front_door_is_served_what_it_is_listed(tmp_path: Path) -> None:
    listed, outcomes = _session(tmp_path, "front_door", "admin", ["hangar_status", "hangar_warm"])

    assert {"hangar_status", "hangar_warm"} <= listed, listed
    for name, (refused, text) in outcomes.items():
        assert not refused, f"{name}: {text}"
        assert "Authentication required" not in text, text


def test_a_declared_viewer_on_a_front_door_is_not_listed_the_lifecycle_tools(tmp_path: Path) -> None:
    listed, _ = _session(tmp_path, "front_door", "viewer", [])

    assert "hangar_status" in listed, listed
    assert "hangar_warm" not in listed, listed


def test_a_declared_admin_on_egress_is_served(tmp_path: Path) -> None:
    _, outcomes = _session(tmp_path, "egress", "admin", ["hangar_warm"])

    refused, text = outcomes["hangar_warm"]
    assert not refused, text


def test_a_declared_viewer_on_egress_is_refused_on_its_roles(tmp_path: Path) -> None:
    _, outcomes = _session(tmp_path, "egress", "viewer", ["hangar_status", "hangar_warm"])

    assert not outcomes["hangar_status"][0], outcomes["hangar_status"][1]
    refused, text = outcomes["hangar_warm"]
    assert refused, text
    assert "Not authorized to call 'hangar_warm': mcp_servers:lifecycle permission required" in text, text
