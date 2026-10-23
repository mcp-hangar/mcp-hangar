"""Tier 2 live verification: ``server/discover`` lists what ``tools/list`` lists, for the same key (#1695).

Black-box against a REAL ``mcp-hangar serve --http`` with
``tool_access.mode: front_door`` and API-key auth on. ``server/discover`` is a
custom route: it gets the authenticated request, but nothing bound the caller
from it, so every authenticated tenant was projected as no one and shown
``tools: []`` while its ``tools/list`` was correct. And the front door's
``tools/list`` also carries the ``hangar_*`` tools the caller may call (#904),
which discovery left out.

A ``viewer`` and a ``developer`` key, under one tenant: each gets the same
names from ``GET`` and ``POST /server/discover`` as from ``tools/list`` over
``/mcp``, and the two callers' management surfaces differ, so the comparison
is not between two empty or two identical lists. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t2_server_discover_matches_tools_list.py -o addopts=""
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t2]

_TENANT = "tenant-discover"
_VIEWER = "svc:discover-viewer"
_DEVELOPER = "svc:discover-developer"

_CONFIG = """\
logging:
  level: WARNING
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
class _Gateway:
    base_url: str
    keys: dict[str, str]


def _seed(auth_db: Path) -> dict[str, str]:
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:
        return {p: store.create_key(principal_id=p, name=p, tenant_id=_TENANT) for p in (_VIEWER, _DEVELOPER)}
    finally:
        store.close()


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Gateway]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("discover_matches_tools_list")
    auth_db = workdir / "auth.db"
    keys = _seed(auth_db)
    config = _CONFIG.format(
        auth_db=auth_db, viewer=_VIEWER, developer=_DEVELOPER, python=sys.executable, server=_MATH_SERVER
    )
    with running_hangar(workdir, config) as hangar:
        yield _Gateway(base_url=hangar.base_url, keys=keys)


def _tools_list(gateway: _Gateway, principal: str) -> set[str]:
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    async def _list() -> set[str]:
        async with open_mcp_streams(f"{gateway.base_url}/mcp", {"X-API-Key": gateway.keys[principal]}) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                return {tool.name for tool in (await session.list_tools()).tools}

    return asyncio.run(asyncio.wait_for(_list(), timeout=60))


def _discovered(gateway: _Gateway, principal: str, method: str) -> set[str]:
    headers = {"X-API-Key": gateway.keys[principal]}
    url = f"{gateway.base_url}/server/discover"
    if method == "GET":
        response = httpx.get(url, headers=headers, timeout=30)
        result = response.json()
    else:
        response = httpx.post(
            url, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "server/discover"}, timeout=30
        )
        result = response.json()["result"]
    assert response.status_code == 200, response.text
    return {tool["name"] for tool in result["tools"]}


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("principal", [_VIEWER, _DEVELOPER])
def test_discovery_lists_what_tools_list_lists(gateway: _Gateway, principal: str, method: str) -> None:
    listed = _tools_list(gateway, principal)

    assert "add" in listed, f"{principal} lists no upstream tool: {sorted(listed)}"
    assert _discovered(gateway, principal, method) == listed


def test_the_two_callers_see_different_surfaces(gateway: _Gateway) -> None:
    # The developer may call management tools the viewer may not, so a check
    # that compared two empty lists, or two copies of one, would fail here.
    assert _discovered(gateway, _DEVELOPER, "GET") - _discovered(gateway, _VIEWER, "GET")


def test_an_anonymous_caller_is_refused(gateway: _Gateway) -> None:
    assert httpx.get(f"{gateway.base_url}/server/discover", timeout=30).status_code == 401
