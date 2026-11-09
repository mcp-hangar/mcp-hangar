"""Tier 0 live verification: a policy set over REST reaches a caller whose policy is cached (#2203).

Black-box against a REAL ``mcp-hangar serve --http``. The caller's tenant is on
a seeded ``X-API-Key``; every call goes through ``hangar_call`` over the shipped
streamable-HTTP ``/mcp`` surface, which resolves and caches that tenant's
effective policy. A provider policy is then set with
``POST /api/auth/policies/provider/<id>`` and the same calls are made again.

The claim: the next call is decided by the policy just set, for a server named
directly and for a group, without a restart. The resolver had dropped only the
cache entries that named the server, so ``_global`` -- read by every scope --
and a policy on a group id -- read by every scope of that group -- did not reach
a caller who had already called.

Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t0" -o addopts=""
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest

from tests.live import _group_support as gs
from tests.live.conftest import _MATH_SERVER, _serve_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

_TENANT = "tenant:cached"

_CONFIG = """\
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
    path: {auth_db}
  role_assignments:
    - principal: "svc:{tenant}"
      role: admin
      scope: global
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
  pool:
    mode: group
    members:
      - id: math
"""


@dataclass
class _Harness:
    base_url: str
    key: str


@pytest.fixture(scope="module")
def hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("policy_cache")
    auth_db = workdir / "auth.db"
    try:
        keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")
    config = _CONFIG.format(auth_db=auth_db, python=sys.executable, server=str(_MATH_SERVER), tenant=_TENANT)
    for base_url in _serve_hangar(workdir, config):
        yield _Harness(base_url=base_url, key=keys[_TENANT])


def _call(harness: _Harness, mcp_server: str, tool: str) -> bool:
    """One call through ``hangar_call`` over streamable-HTTP; whether it succeeded."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    calls = [{"mcp_server": mcp_server, "tool": tool, "arguments": {"a": 2, "b": 3}}]

    async def _run():
        async with open_mcp_streams(f"{harness.base_url}/mcp", {"X-API-Key": harness.key}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("hangar_call", {"calls": calls})

    result = asyncio.run(_run())
    text = "".join(getattr(block, "text", "") or "" for block in result.content or [])
    (outcome,) = json.loads(text)["results"]
    return bool(outcome["success"])


def _deny(harness: _Harness, target_id: str, tool: str) -> None:
    answer = httpx.post(
        f"{harness.base_url}/api/auth/policies/provider/{target_id}",
        headers={"X-API-Key": harness.key},
        json={"deny_list": [tool]},
        timeout=10.0,
    )
    assert answer.status_code == 200, answer.text


def test_a_policy_on_a_group_id_reaches_a_cached_group_caller(hangar: _Harness) -> None:
    assert _call(hangar, "pool", "multiply")
    assert _call(hangar, "math", "multiply")

    _deny(hangar, "pool", "multiply")

    assert not _call(hangar, "pool", "multiply")
    # `math` is asked under its own scope and under the group that owns it.
    assert not _call(hangar, "math", "multiply")


def test_a_global_policy_reaches_every_cached_caller(hangar: _Harness) -> None:
    assert _call(hangar, "math", "add")
    assert _call(hangar, "pool", "add")

    _deny(hangar, "_global", "add")

    assert not _call(hangar, "math", "add")
    assert not _call(hangar, "pool", "add")
