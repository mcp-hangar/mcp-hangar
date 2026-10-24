"""Tier 0 live verification: a front-door listing shows what a call would be allowed (#1784, #1763).

Over real streamable-HTTP, with API-key tenants, against a group of two
subprocess members of ``examples/provider_math``: ``member-b`` denies ``power``
by its own policy, ``member-a`` withdraws ``divide`` for one tenant, and two
tenants are pinned, one to each member. For each tenant, ``tools/list``,
``server/discover`` and ``hangar_tools`` on the group are compared with what a
flat call to every name does:

- a pinned tenant is routed to one member, so its listing is exactly what its
  calls are allowed;
- a load-balanced tenant may be routed to either member, so its listing is
  what BOTH members allow: every listed name is callable, and a name one member
  refuses is not listed, although a call may still land on the other member
  (decision 1 on #1763).

A mocked request context has missed exactly this class of per-tenant fail-open
before, which is why this runs over the shipped transport.

Run on demand::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_http_listing_matches_call.py -o addopts=""
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

from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

GROUP, A, B = "pool", "member-a", "member-b"
LB, PIN_A, PIN_B = "tenant-lb", "tenant-pin-a", "tenant-pin-b"
TENANTS = (LB, PIN_A, PIN_B)
#: Every tool the stub backend serves, with arguments its schema accepts.
TOOLS: dict[str, dict[str, Any]] = {
    "add": {"a": 2, "b": 3},
    "subtract": {"a": 2, "b": 3},
    "multiply": {"a": 2, "b": 3},
    "divide": {"a": 2, "b": 3},
    "power": {"base": 2, "exponent": 3},
}
#: What each tenant's listing must be. `power`: member-b denies it, so only a
#: tenant pinned to member-a sees it. `divide`: withdrawn on member-a for the
#: load-balanced tenant alone.
LISTED = {
    LB: {"add", "subtract", "multiply"},
    PIN_A: set(TOOLS),
    PIN_B: {"add", "subtract", "multiply", "divide"},
}

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
    - principal: "svc:{lb}"
      role: developer
      scope: global
    - principal: "svc:{pin_a}"
      role: developer
      scope: global
    - principal: "svc:{pin_b}"
      role: developer
      scope: global
mcp_servers:
  {a}:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 120
    tool_projection:
      tenant_overrides:
        "{lb}":
          withdrawn: [divide]
  {b}:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 120
  {group}:
    mode: group
    strategy: round_robin
    auto_start: true
    members:
      - id: {a}
      - id: {b}
        tools:
          deny_list: [power]
    canary:
      member: {b}
      split_pct: 0
      pinned_tenants:
        "{pin_a}": {a}
        "{pin_b}": {b}
"""


@dataclass
class _Gateway:
    base_url: str
    keys: dict[str, str]


def _seed(auth_db: Path) -> dict[str, str]:
    """One key per tenant; its principal is `svc:<tenant>`, which the config gives the developer role."""
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:
        return {
            tenant: store.create_key(principal_id=f"svc:{tenant}", name=tenant, tenant_id=tenant) for tenant in TENANTS
        }
    finally:
        store.close()


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Gateway]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("listing_matches_call")
    auth_db = workdir / "auth.db"
    try:
        keys = _seed(auth_db)
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed API keys: {exc}")
    config = _CONFIG.format(
        auth_db=str(auth_db),
        lb=LB,
        pin_a=PIN_A,
        pin_b=PIN_B,
        a=A,
        b=B,
        group=GROUP,
        python=sys.executable,
        server=str(_MATH_SERVER),
    )
    with running_hangar(workdir, config) as hangar:
        yield _Gateway(base_url=hangar.base_url, keys=keys)


def _session(gateway: _Gateway, tenant: str) -> Any:
    from tests.live._mcp_client import open_mcp_streams

    return open_mcp_streams(f"{gateway.base_url}/mcp", {"X-API-Key": gateway.keys[tenant]})


def _tools_list(gateway: _Gateway, tenant: str) -> set[str]:
    """The backend's tools `tools/list` shows *tenant*; the `hangar_*` management tools are not under test."""
    from mcp import ClientSession

    async def _list() -> set[str]:
        async with _session(gateway, tenant) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return {tool.name for tool in (await session.list_tools()).tools} & set(TOOLS)

    return asyncio.run(asyncio.wait_for(_list(), timeout=60))


def _dict_with(result: Any, key: str) -> dict[str, Any]:
    """The dict carrying *key* in a tool call's result, structured or as JSON text."""
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        inner = structured if key in structured else structured.get("result")
        if isinstance(inner, dict) and key in inner:
            return inner
    for block in getattr(result, "content", None) or []:
        try:
            data = json.loads(getattr(block, "text", None) or "")
        except ValueError:
            continue
        if isinstance(data, dict) and key in data:
            return data
    raise AssertionError(f"no dict with {key!r} in {result!r}")


def _hangar_tools(gateway: _Gateway, tenant: str) -> set[str]:
    """What `hangar_tools` on the group shows *tenant*, over the same transport as the flat listing."""
    from mcp import ClientSession

    async def _list() -> set[str]:
        async with _session(gateway, tenant) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("hangar_tools", {"mcp_server": GROUP})
        return {tool["name"] for tool in _dict_with(result, "tools")["tools"]} & set(TOOLS)

    return asyncio.run(asyncio.wait_for(_list(), timeout=60))


def _discovered(gateway: _Gateway, tenant: str) -> set[str]:
    response = httpx.get(f"{gateway.base_url}/server/discover", headers={"X-API-Key": gateway.keys[tenant]}, timeout=30)
    assert response.status_code == 200, response.text
    return {tool["name"] for tool in response.json()["tools"]} & set(TOOLS)


def _callable(gateway: _Gateway, tenant: str) -> set[str]:
    """The names of `TOOLS` a flat call succeeds on for *tenant*, each in its own session."""
    from mcp import ClientSession

    from mcp_hangar._sdk_compat import McpError

    async def _call(tool: str) -> bool:
        async with _session(gateway, tenant) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                try:
                    result = await session.call_tool(tool, TOOLS[tool])
                except McpError:
                    return False  # -32601: unknown to this caller, as a denied name must be (#905)
        return not bool(getattr(result, "is_error", False) or getattr(result, "isError", False))

    async def _all() -> set[str]:
        return {tool for tool in TOOLS if await _call(tool)}

    return asyncio.run(asyncio.wait_for(_all(), timeout=120))


@pytest.mark.parametrize("tenant", TENANTS)
def test_the_listing_is_what_the_tenant_is_allowed(gateway: _Gateway, tenant: str) -> None:
    assert _tools_list(gateway, tenant) == LISTED[tenant]


@pytest.mark.parametrize("tenant", TENANTS)
def test_discovery_agrees_with_the_listing(gateway: _Gateway, tenant: str) -> None:
    assert _discovered(gateway, tenant) == _tools_list(gateway, tenant)


@pytest.mark.parametrize("tenant", TENANTS)
def test_hangar_tools_agrees_with_the_listing(gateway: _Gateway, tenant: str) -> None:
    """The management tool lists the group as the front door does: on the members this tenant may be routed to."""
    assert _hangar_tools(gateway, tenant) == _tools_list(gateway, tenant)


@pytest.mark.parametrize("tenant", (PIN_A, PIN_B))
def test_a_pinned_tenant_can_call_exactly_what_it_is_shown(gateway: _Gateway, tenant: str) -> None:
    assert _callable(gateway, tenant) == _tools_list(gateway, tenant)


def test_a_load_balanced_tenant_can_call_everything_it_is_shown(gateway: _Gateway) -> None:
    """Listed means callable. A name one member refuses is not listed, whichever member a call then gets."""
    listed = _tools_list(gateway, LB)
    callable_now = _callable(gateway, LB)

    assert listed <= callable_now, f"listed but refused: {sorted(listed - callable_now)}"
    assert "power" not in listed and "divide" not in listed
