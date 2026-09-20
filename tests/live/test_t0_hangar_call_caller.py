"""Tier 0 live verification: the caller of a ``hangar_call`` reaches the executor (#1453).

``hangar_call`` reads its caller off the request: the principal for the
``tool:invoke`` check, and the identity whose tenant the executor's per-tenant
controls apply to. Since #1453 it hands both to the body the Python facade's
``invoke`` also runs. Driven black-box against a *running* hangar (real CLI
subprocess, real MCP over streamable-HTTP), with authentication on:

* each tenant is charged to its own execution budget: over its rate it is
  refused with ``TenantQuotaExceeded``, while another tenant's call still runs.
  The two callers differ only in the API key they present, so the refusal
  shows the tenant that key names reached the budget in the executor;
* an anonymous caller is refused by the ``tool:invoke`` check with
  ``AuthorizationDenied``.

Per-tenant withdrawal on the same path is ``test_t0_withdrawal.py``. Skip-safe
as the rest of T0. See ``docs/internal/LIVE_VERIFICATION.md``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from tests.live import _group_support as gs
from tests.live.conftest import _MATH_SERVER, _serve_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
TOO_FAST = "This tenant's execution budget is exhausted: calls started too fast"

# Auth on, with anonymous callers let through the transport, so that the
# `tool:invoke` check in `hangar_call` is what answers them. Each tenant's key
# principal (`svc:<tenant>`) holds `developer`, which carries `tool:invoke`.
# Tenant A may start one call and then no more; tenant B is not constrained.
_CALLER_CONFIG = """\
logging:
  level: WARNING
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
    - principal: "svc:{tenant_a}"
      role: developer
      scope: global
    - principal: "svc:{tenant_b}"
      role: developer
      scope: global
execution:
  tenant_limits:
    "{tenant_a}": {{max_concurrency: 4, rps: 0.001, burst: 1}}
    "{tenant_b}": {{max_concurrency: 4, rps: 1000, burst: 1000}}
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
"""


def _hangar_call(base_url: str, api_key: str | None, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One ``hangar_call`` over streamable-HTTP, with *api_key* or none; its result[0]."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    headers = {"X-API-Key": api_key} if api_key else {}

    async def _call() -> dict[str, Any]:
        async with open_mcp_streams(f"{base_url}/mcp", headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "hangar_call",
                    {"calls": [{"mcp_server": "math", "tool": tool, "arguments": arguments}]},
                )
                data = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
                if not (isinstance(data, dict) and isinstance(data.get("results"), list)):
                    text = "".join(getattr(c, "text", "") for c in result.content if getattr(c, "type", None) == "text")
                    data = json.loads(text) if text else {}
                assert isinstance(data, dict) and isinstance(data.get("results"), list), f"no batch result: {data!r}"
                first: dict[str, Any] = data["results"][0]
                return first

    return asyncio.run(_call())


@pytest.fixture(scope="module")
def caller_hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, dict[str, str]]]:
    """Start hangar with per-tenant budgets and auth on; yield (base_url, tenant_keys)."""
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    workdir = tmp_path_factory.mktemp("live_hangar_call_caller")
    auth_db = workdir / "auth.db"
    try:
        tenant_keys = gs.seed_tenant_keys(auth_db, [TENANT_A, TENANT_B])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")

    config_text = _CALLER_CONFIG.format(
        auth_db=str(auth_db),
        tenant_a=TENANT_A,
        tenant_b=TENANT_B,
        python=sys.executable,
        server=str(Path(_MATH_SERVER)),
    )
    server = cast(Generator[str, None, None], _serve_hangar(workdir, config_text))
    base_url = next(server)
    try:
        yield base_url, tenant_keys
    finally:
        server.close()


def test_each_tenant_is_charged_to_its_own_budget(caller_hangar) -> None:
    """Claim: a tenant's ``hangar_call`` is charged to that tenant's budget.

    Tenant A's first call spends its one token and runs; its second is refused
    as over its rate. Tenant B, with a budget of its own, still runs.
    """
    base_url, keys = caller_hangar

    first = _hangar_call(base_url, keys[TENANT_A], "add", {"a": 2, "b": 3})
    assert first["success"] is True, first

    second = _hangar_call(base_url, keys[TENANT_A], "add", {"a": 2, "b": 3})
    assert (second["success"], second["error_type"], second["error"]) == (False, "TenantQuotaExceeded", TOO_FAST)

    other = _hangar_call(base_url, keys[TENANT_B], "add", {"a": 2, "b": 3})
    assert other["success"] is True, other


def test_an_anonymous_caller_is_refused_by_the_tool_invoke_check(caller_hangar) -> None:
    """Claim: with auth configured, an anonymous ``hangar_call`` is denied ``tool:invoke``."""
    base_url, _keys = caller_hangar

    result = _hangar_call(base_url, None, "add", {"a": 2, "b": 3})

    assert (result["success"], result["error_type"], result["error"]) == (
        False,
        "AuthorizationDenied",
        "Authentication required to invoke tools",
    ), result
