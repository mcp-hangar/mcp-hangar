"""Tier 0 live verification: a server deleted over REST takes its tool-access policy with it (#2298).

Black-box against a REAL ``mcp-hangar serve --http`` with a SQLite auth store
and no lease keeper. A server is created with ``POST /api/mcp_servers/``, a
tool is denied on it with ``POST /api/auth/policies/provider/<id>``, and the
server is deleted with ``DELETE /api/mcp_servers/<id>`` and created again under
the same id. Every call goes through ``hangar_call`` over the shipped
streamable-HTTP ``/mcp`` surface.

The claim: the new server is governed by no policy of the old one, without a
restart, and the stored row is gone, so a restart cannot replay it. The delete
used to clear neither the resolver nor the store.

Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t0" -o addopts=""
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tests.live import _group_support as gs
from tests.live.conftest import _MATH_SERVER, _serve_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

_TENANT = "tenant:operator"
_SERVER = "made-over-rest"

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
mcp_servers: {{}}
"""


@dataclass
class _Harness:
    base_url: str
    key: str

    def api(self, method: str, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
        return httpx.request(
            method, f"{self.base_url}/api{path}", json=body, headers={"X-API-Key": self.key}, timeout=30.0
        )


@pytest.fixture(scope="module")
def hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("rest_delete_policy")
    auth_db = workdir / "auth.db"
    try:
        keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")
    for base_url in _serve_hangar(workdir, _CONFIG.format(auth_db=auth_db, tenant=_TENANT)):
        yield _Harness(base_url=base_url, key=keys[_TENANT])


def _create(harness: _Harness) -> None:
    body = {
        "mcp_server_id": _SERVER,
        "mode": "subprocess",
        "command": [sys.executable, str(_MATH_SERVER)],
        "env": {"MCP_TRANSPORT": "stdio"},
        "idle_ttl_s": 60,
    }
    created = harness.api("POST", "/mcp_servers/", body)
    assert created.status_code == 201, created.text


def _call(harness: _Harness, tool: str) -> bool:
    """One call through ``hangar_call`` over streamable-HTTP; whether it succeeded."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    calls = [{"mcp_server": _SERVER, "tool": tool, "arguments": {"a": 2, "b": 3}}]

    async def _run():
        async with open_mcp_streams(f"{harness.base_url}/mcp", {"X-API-Key": harness.key}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("hangar_call", {"calls": calls})

    result = asyncio.run(_run())
    text = "".join(getattr(block, "text", "") or "" for block in result.content or [])
    (outcome,) = json.loads(text)["results"]
    return bool(outcome["success"])


def _stored(harness: _Harness) -> bool:
    answer = harness.api("GET", f"/auth/policies/provider/{_SERVER}")
    assert answer.status_code == 200, answer.text
    return bool(answer.json()["found"])


def test_a_server_created_again_after_a_rest_delete_has_none_of_its_policy(hangar: _Harness) -> None:
    _create(hangar)
    assert _call(hangar, "add")
    denied = hangar.api("POST", f"/auth/policies/provider/{_SERVER}", {"deny_list": ["add"]})
    assert denied.status_code == 200, denied.text
    assert not _call(hangar, "add"), "the policy set over REST did not apply"
    assert _stored(hangar)

    deleted = hangar.api("DELETE", f"/mcp_servers/{_SERVER}")
    assert deleted.status_code == 200, deleted.text
    assert not _stored(hangar), "the stored row outlived the server"

    _create(hangar)
    assert _call(hangar, "add"), "the new server inherited the deleted one's deny list"
