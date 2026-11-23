"""Tier 0 live verification: a server a reload declares again keeps the L7 policy its row holds (#2332).

Black-box against a REAL ``mcp-hangar serve --http`` whose fleet is kept in
SQLite (``persistence.backend: sqlite``, which keeps the API keys too), with
no lease keeper. The file
declares a server; an L7 policy that allows only ``multiply`` is pushed with
``PUT /api/mcp_servers/<id>/l7_policy``, which writes the server's row. The file
then drops the server and is reloaded with ``POST /api/config/reload``, and
declares it again and is reloaded once more. Every call goes through
``hangar_call`` over the shipped streamable-HTTP ``/mcp`` surface.

The claim: the server the second reload adds refuses ``add`` from its first
call, as a restart on the same file would. The reload used to build it from the
file alone, so it served every tool until the operator's next push.

The same run over PostgreSQL, two replicas' database, is in
``test_t4_two_replicas.py``.

Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t0" -o addopts=""
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.live import _group_support as gs
from tests.live.conftest import _MATH_SERVER, _serve_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

_TENANT = "tenant:operator"
_SERVER = "declared-again"
#: Allows `multiply` only, so `add` is outside the policy.
_POLICY = {"tools": {"allow": ["multiply"]}, "defaultAction": "Deny", "mode": "Enforce"}

_CONFIG = """\
logging:
  level: WARNING
persistence:
  backend: sqlite
  sqlite:
    data_dir: data
    auth_path: {auth_db}
auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  role_assignments:
    - principal: "svc:{tenant}"
      role: admin
      scope: global
mcp_servers: {servers}
"""


@dataclass
class _Harness:
    base_url: str
    key: str
    workdir: Path
    auth_db: Path

    def api(self, method: str, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
        return httpx.request(
            method, f"{self.base_url}/api{path}", json=body, headers={"X-API-Key": self.key}, timeout=30.0
        )

    def write_config(self, *, declares: bool) -> None:
        (self.workdir / "config.yaml").write_text(_config(self.auth_db, declares=declares))

    def reload(self, *, declares: bool) -> None:
        self.write_config(declares=declares)
        reloaded = self.api("POST", "/config/reload", {})
        assert reloaded.status_code == 200, reloaded.text

    def held(self) -> bool:
        return self.api("GET", f"/mcp_servers/{_SERVER}").status_code == 200


def _config(auth_db: Path, *, declares: bool) -> str:
    servers = "{}"
    if declares:
        servers = (
            f"\n  {_SERVER}:\n    mode: subprocess\n    command: [{json.dumps(sys.executable)}, "
            f"{json.dumps(str(_MATH_SERVER))}]\n    env:\n      MCP_TRANSPORT: stdio\n    idle_ttl_s: 60"
        )
    return _CONFIG.format(auth_db=auth_db, tenant=_TENANT, servers=servers)


@pytest.fixture(scope="module")
def hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("reload_declares_again")
    auth_db = workdir / "auth.db"
    try:
        keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")
    for base_url in _serve_hangar(workdir, _config(auth_db, declares=True)):
        yield _Harness(base_url=base_url, key=keys[_TENANT], workdir=workdir, auth_db=auth_db)


def _call(harness: _Harness, tool: str) -> dict[str, Any]:
    """One call through ``hangar_call`` over streamable-HTTP; the call's result."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    calls = [{"mcp_server": _SERVER, "tool": tool, "arguments": {"a": 2, "b": 3}}]

    async def _run():
        async with open_mcp_streams(f"{harness.base_url}/mcp", {"X-API-Key": harness.key}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("hangar_call", {"calls": calls})

    result = asyncio.run(asyncio.wait_for(_run(), timeout=60))
    text = "".join(getattr(block, "text", "") or "" for block in result.content or [])
    (outcome,) = json.loads(text)["results"]
    return dict(outcome)


def _refused_by_the_policy(outcome: dict[str, Any]) -> bool:
    return outcome.get("success") is False and outcome.get("error_type") == "EgressPolicyDeniedError"


def test_a_server_the_file_drops_and_declares_again_refuses_a_tool_outside_its_policy(hangar: _Harness) -> None:
    assert _call(hangar, "add").get("success") is True, "the server does not serve before any policy"
    pushed = hangar.api("PUT", f"/mcp_servers/{_SERVER}/l7_policy", _POLICY)
    assert pushed.status_code == 200, pushed.text
    assert _refused_by_the_policy(outcome := _call(hangar, "add")), f"the pushed policy did not apply: {outcome}"

    hangar.reload(declares=False)
    assert not hangar.held(), "the reload of a file without the server kept it"

    hangar.reload(declares=True)
    assert hangar.held(), "the reload of a file that declares the server again did not add it"

    outcome = _call(hangar, "add")
    assert _refused_by_the_policy(outcome), f"the server declared again served a tool outside its policy: {outcome}"
    assert _call(hangar, "multiply").get("success") is True, "and it serves the tool its policy allows"
