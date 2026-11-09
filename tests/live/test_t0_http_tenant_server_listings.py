"""Tier 0 live verification: a tenant's server listings show only the servers its calls may reach (#2161).

Over real streamable-HTTP, with API-key tenants, against a group ``pool`` of
two subprocess members of ``examples/provider_math`` declared at the top level
of the configuration, and a third server ``solo`` that no group owns. One
tenant is pinned to ``member-a``; another is load-balanced over both members.
For each, ``hangar_list``, ``hangar_status``, ``hangar_health`` and
``hangar_start`` on the group are read over the shipped transport:

- the pinned tenant is shown ``member-a`` and ``solo``, never ``member-b``, and
  every group count it is told is over ``member-a`` alone, with no
  ``circuit_open``;
- the load-balanced tenant is shown both members and ``solo``;
- ``hangar_group_rebalance`` refuses a tenant caller that holds the permission
  for it, and rebalances for a caller without a tenant;
- a caller without a tenant is shown every server and the whole group.

A mocked request context has missed this class of per-tenant fail-open before,
which is why this runs over the shipped transport.

Run on demand::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_http_tenant_server_listings.py -o addopts=""
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

pytestmark = [pytest.mark.live, pytest.mark.t0]

GROUP, A, B, SOLO = "pool", "member-a", "member-b", "solo"
PIN_A, LB, OPS, ADMIN = "tenant-pin-a", "tenant-lb", "tenant-ops", "operator"

_SERVER = """\
  {sid}:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 120
"""

_CONFIG = """\
logging:
  level: WARNING
tool_access:
  mode: egress
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
    - principal: "svc:{pin_a}"
      role: developer
      scope: global
    - principal: "svc:{lb}"
      role: developer
      scope: global
    - principal: "svc:{ops}"
      role: provider-admin
      scope: global
    - principal: "svc:{admin}"
      role: admin
      scope: global
mcp_servers:
{servers}  {group}:
    mode: group
    strategy: round_robin
    auto_start: true
    members:
      - id: {a}
      - id: {b}
    canary:
      member: {b}
      split_pct: 0
      pinned_tenants:
        "{pin_a}": {a}
"""


@dataclass
class _Gateway:
    base_url: str
    keys: dict[str, str]


def _seed(auth_db: Path) -> dict[str, str]:
    """One key per caller; its principal is `svc:<name>`. The operator's key carries no tenant."""
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:
        keys = {
            tenant: store.create_key(principal_id=f"svc:{tenant}", name=tenant, tenant_id=tenant)
            for tenant in (PIN_A, LB, OPS)
        }
        keys[ADMIN] = store.create_key(principal_id=f"svc:{ADMIN}", name=ADMIN)
        return keys
    finally:
        store.close()


def _session(gateway: _Gateway, caller: str) -> Any:
    from tests.live._mcp_client import open_mcp_streams

    return open_mcp_streams(f"{gateway.base_url}/mcp", {"X-API-Key": gateway.keys[caller]})


def _payload(result: Any) -> dict[str, Any]:
    """A management tool's result as a dict, structured or as JSON text."""
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        inner = structured.get("result", structured) if set(structured) == {"result"} else structured
        if isinstance(inner, dict):
            return inner
    for block in getattr(result, "content", None) or []:
        try:
            data = json.loads(getattr(block, "text", None) or "")
        except ValueError:
            return {"error": getattr(block, "text", "")}
        if isinstance(data, dict):
            return data
    raise AssertionError(f"no dict in {result!r}")


def _call(gateway: _Gateway, caller: str, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    from mcp import ClientSession

    async def _run() -> dict[str, Any]:
        async with _session(gateway, caller) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments or {})
        payload = _payload(result)
        if getattr(result, "is_error", False) or getattr(result, "isError", False):
            payload.setdefault("error", payload)
        return payload

    return asyncio.run(asyncio.wait_for(_run(), timeout=90))


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Gateway]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("tenant_server_listings")
    auth_db = workdir / "auth.db"
    try:
        keys = _seed(auth_db)
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed API keys: {exc}")
    servers = "".join(_SERVER.format(sid=sid, python=sys.executable, server=str(_MATH_SERVER)) for sid in (A, B, SOLO))
    config = _CONFIG.format(
        auth_db=str(auth_db), pin_a=PIN_A, lb=LB, ops=OPS, admin=ADMIN, a=A, b=B, group=GROUP, servers=servers
    )
    with running_hangar(workdir, config) as hangar:
        live = _Gateway(base_url=hangar.base_url, keys=keys)
        # Both members ready and in rotation, so the load-balanced tenant may be routed to either.
        started = _call(live, ADMIN, "hangar_start", {"mcp_server": GROUP})
        assert started.get("members_started") == 2, started
        yield live


def _server_ids(listing: dict[str, Any], key: str) -> set[str]:
    return {str(entry[key]) for entry in listing["mcp_servers"]}


def _pool(groups: list[dict[str, Any]], key: str = "id") -> dict[str, Any]:
    return next(group for group in groups if group[key] == GROUP)


def test_hangar_list_shows_a_pinned_tenant_its_member_and_the_server_no_group_owns(gateway: _Gateway) -> None:
    assert _server_ids(_call(gateway, PIN_A, "hangar_list"), "mcp_server_id") == {A, SOLO}


def test_hangar_list_shows_a_load_balanced_tenant_both_members(gateway: _Gateway) -> None:
    assert _server_ids(_call(gateway, LB, "hangar_list"), "mcp_server_id") == {A, B, SOLO}


def test_hangar_status_lists_and_counts_only_what_a_pinned_tenant_may_reach(gateway: _Gateway) -> None:
    status = _call(gateway, PIN_A, "hangar_status")

    assert _server_ids(status, "id") == {A, SOLO}
    assert status["summary"]["total_mcp_servers"] == 2
    pool = _pool(status["groups"])
    assert pool["total_members"] == pool["healthy_members"] == pool["members_in_rotation_count"] == 1
    assert "circuit_open" not in pool
    assert B not in status["formatted"]


def test_hangar_health_counts_only_what_a_pinned_tenant_may_reach(gateway: _Gateway) -> None:
    health = _call(gateway, PIN_A, "hangar_health")

    assert health["mcp_servers"]["total"] == 2
    assert health["groups"]["total_members"] == health["groups"]["healthy_members"] == 1


def test_hangar_start_on_the_group_reports_a_pinned_tenant_its_member(gateway: _Gateway) -> None:
    started = _call(gateway, PIN_A, "hangar_start", {"mcp_server": GROUP})

    assert started["total_members"] == started["healthy_count"] == started["members_started"] == 1


def test_hangar_group_rebalance_refuses_a_tenant_caller(gateway: _Gateway) -> None:
    """`tenant-ops` holds `group:update` through provider-admin; its tenant is what refuses it."""
    refused = _call(gateway, OPS, "hangar_group_rebalance", {"group": GROUP})

    assert "error" in refused and "members_in_rotation" not in refused, refused
    assert "rebalance" in str(refused["error"]) and B not in str(refused["error"])


def test_a_caller_without_a_tenant_is_shown_the_whole_fleet(gateway: _Gateway) -> None:
    assert _server_ids(_call(gateway, ADMIN, "hangar_list"), "mcp_server_id") == {A, B, SOLO}
    status = _call(gateway, ADMIN, "hangar_status")
    assert _server_ids(status, "id") == {A, B, SOLO}
    assert _pool(status["groups"])["total_members"] == 2
    assert _pool(status["groups"])["circuit_open"] is False
    rebalanced = _call(gateway, ADMIN, "hangar_group_rebalance", {"group": GROUP})
    assert sorted(rebalanced["members_in_rotation"]) == [A, B]
