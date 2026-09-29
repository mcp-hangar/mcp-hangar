"""Tier 2 live verification: the front door's flat call checks ``tool:invoke`` (#1622).

Black-box against a REAL ``mcp-hangar serve --http`` with
``tool_access.mode: front_door`` and API-key auth on (``allow_anonymous:
false``). Two keys are seeded into its SQLite store under one tenant, and
``role_assignments`` bind one principal to ``viewer`` and the other to
``developer``. Each calls the upstream tool ``add`` by its flat name over the
shipped streamable-HTTP ``/mcp`` surface.

* ``viewer`` lacks ``tool:invoke``: the call is a tool error naming the
  permission, and the server's call log records it as ``AuthorizationDenied``.
  ``hangar_call`` refused this caller since #389; the flat call served it.
* ``developer`` holds it: the call is served.

API keys rather than Keycloak, so the check runs wherever this checkout does;
the JWT principal is covered by the unit tier. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t2_front_door_tool_invoke.py -m "live and t2" -o addopts=""
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t2]

_TENANT = "tenant-invoke"
_VIEWER = "svc:invoke-viewer"
_DEVELOPER = "svc:invoke-developer"
_REFUSAL = "Not authorized to invoke tool 'add': tool:invoke permission required"

_CONFIG = """\
logging:
  level: INFO
  json_format: true
tool_access:
  mode: front_door
auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {auth_db}
  role_assignments:
    - principal: "{viewer}"
      role: viewer
      scope: global
    - principal: "{developer}"
      role: developer
      scope: global
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
"""


@dataclass
class _FrontDoor:
    base_url: str
    keys: dict[str, str]
    log_path: Path

    def call_lines(self) -> list[dict[str, Any]]:
        lines = []
        for raw in self.log_path.read_text(errors="replace").splitlines():
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if line.get("event") == "front_door_tool_call":
                lines.append(line)
        return lines


def _seed(auth_db: Path) -> dict[str, str]:
    """One key per principal, in one tenant and in no group, so its role comes only from config."""
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:
        return {
            principal: store.create_key(principal_id=principal, name=principal, tenant_id=_TENANT)
            for principal in (_VIEWER, _DEVELOPER)
        }
    finally:
        store.close()


@pytest.fixture(scope="module")
def front_door(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_FrontDoor]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("front_door_tool_invoke")
    auth_db = workdir / "auth.db"
    try:
        keys = _seed(auth_db)
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed API keys: {exc}")
    config = _CONFIG.format(
        auth_db=str(auth_db), viewer=_VIEWER, developer=_DEVELOPER, python=sys.executable, server=str(_MATH_SERVER)
    )
    with running_hangar(workdir, config) as hangar:
        yield _FrontDoor(base_url=hangar.base_url, keys=keys, log_path=hangar.log_path)


def _call_add(front_door: _FrontDoor, principal: str) -> tuple[bool, str]:
    """One flat ``add`` in its own MCP session: (isError, the result's text)."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    async def _run() -> tuple[bool, str]:
        headers = {"X-API-Key": front_door.keys[principal]}
        async with open_mcp_streams(f"{front_door.base_url}/mcp", headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("add", {"a": 2, "b": 3})
        text = " ".join(getattr(block, "text", "") or "" for block in result.content)
        return bool(getattr(result, "is_error", False) or getattr(result, "isError", False)), text

    return asyncio.run(asyncio.wait_for(_run(), timeout=60))


def test_a_viewer_is_refused_the_flat_call(front_door: _FrontDoor) -> None:
    before = len(front_door.call_lines())

    is_error, text = _call_add(front_door, _VIEWER)

    assert (is_error, text) == (True, _REFUSAL)
    [line] = front_door.call_lines()[before:]
    assert (line["principal_id"], line["outcome"], line["reason"]) == (_VIEWER, "tool_error", "AuthorizationDenied")


def test_a_developer_is_served_the_flat_call(front_door: _FrontDoor) -> None:
    before = len(front_door.call_lines())

    is_error, text = _call_add(front_door, _DEVELOPER)

    assert not is_error, text
    assert "5" in text, text
    [line] = front_door.call_lines()[before:]
    assert (line["principal_id"], line["outcome"]) == (_DEVELOPER, "ok")
