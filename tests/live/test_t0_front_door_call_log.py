"""Tier 0 live verification: every front-door call leaves one log line (#1362).

BLACK-BOX against a REAL ``mcp-hangar serve --http`` over the shipped
streamable-HTTP ``/mcp`` surface with ``tool_access.mode: front_door``. The
caller's tenant rides on a seeded ``X-API-Key``, and a per-tenant deny keeps
``power`` out of that tenant's projection. The server's own JSON log is read
back. It must hold exactly one ``front_door_tool_call`` line per call, naming the
tool and the caller, and that includes the denied call, which the caller sees as a
plain ``-32601``.

Per-request behaviour on this path has had fail-opens that a unit test with a
mock context did not see, which is why this is driven over the real transport.
Skip-safe like the rest of the tier. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_front_door_call_log.py -m "live and t0" -o addopts=""
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any

import pytest

from tests.live import _group_support as gs
from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

_TENANT = "tenant-log"
_DENIED = "power"
#: In the arguments of the calls that never reach an upstream. Never in the log.
_CANARY = "CANARY-1362-live"

_CONFIG = """\
logging:
  level: INFO
  json_format: true
tool_access:
  mode: front_door
auth:
  enabled: true
  allow_anonymous: true
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {auth_db}
  role_assignments:
    - principal: "svc:{tenant}"
      role: developer
      scope: global
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
    tool_access:
      member:
        "{tenant}":
          deny_list: [{denied}]
"""


@dataclass
class _FrontDoor:
    base_url: str
    api_key: str
    log_path: Path

    def call_lines(self) -> list[dict[str, Any]]:
        lines = []
        for raw in self.log_path.read_text(errors="replace").splitlines():
            if not raw.startswith("{"):
                continue
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if line.get("event") == "front_door_tool_call":
                lines.append(line)
        return lines


@pytest.fixture(scope="module")
def front_door(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_FrontDoor]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("front_door_call_log")
    auth_db = workdir / "auth.db"
    try:
        keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")

    config = _CONFIG.format(
        auth_db=str(auth_db), tenant=_TENANT, python=sys.executable, server=str(_MATH_SERVER), denied=_DENIED
    )
    with running_hangar(workdir, config) as hangar:
        yield _FrontDoor(base_url=hangar.base_url, api_key=keys[_TENANT], log_path=hangar.log_path)


def _call_all(front_door: _FrontDoor, calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """Make every call in one MCP session; return how each ended, as the client saw it."""
    from mcp import ClientSession

    from mcp_hangar._sdk_compat import McpError
    from tests.live._mcp_client import open_mcp_streams

    async def _run() -> list[str]:
        seen = []
        async with open_mcp_streams(f"{front_door.base_url}/mcp", {"X-API-Key": front_door.api_key}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for tool, arguments in calls:
                    try:
                        result = await session.call_tool(tool, arguments)
                    except McpError as exc:
                        seen.append(f"jsonrpc {exc.error.code}")
                        continue
                    seen.append("isError" if getattr(result, "is_error", False) else "result")
        return seen

    return asyncio.run(_run())


def test_every_call_leaves_one_line_naming_the_tool_and_the_caller(front_door: _FrontDoor) -> None:
    calls = [
        ("add", {"a": 1, "b": 2}),
        ("add", {"a": 3, "b": 4}),
        ("add", {"a": 5, "b": 6}),
        ("divide", {"a": 1, "b": 0}),
        (_DENIED, {"base": 2, "exponent": 3, "note": _CANARY}),
        ("no_such_tool", {"note": _CANARY}),
    ]
    before = len(front_door.call_lines())

    seen = _call_all(front_door, calls)

    # The caller: denied and missing are the same -32601.
    assert seen[4] == seen[5] == "jsonrpc -32601", seen
    lines = front_door.call_lines()[before:]
    assert [line["tool"] for line in lines] == [tool for tool, _ in calls], lines
    assert {(line["principal_id"], line["tenant_id"]) for line in lines} == {(f"svc:{_TENANT}", _TENANT)}
    assert [line["outcome"] for line in lines[:3]] == ["ok", "ok", "ok"], lines
    assert lines[3]["outcome"] == "tool_error", lines[3]
    assert (lines[4]["outcome"], lines[4]["reason"]) == ("not_found", "not_projected")
    assert (lines[5]["outcome"], lines[5]["reason"]) == ("not_found", "unknown")
    assert all(line["request_id"] is not None and line["level"] == "info" for line in lines), lines
    assert _CANARY not in front_door.log_path.read_text(errors="replace")
