"""Tier 0 live verification: per-tenant tool withdrawal on the ``hangar_call`` path.

Driven black-box against a *running* hangar (real CLI subprocess + real MCP over
HTTP). A tool withdrawn for one tenant, invoked by that tenant, must be REJECTED
with ``CallResult(success=False)`` / ``ToolWithdrawnError``; a different tenant
with no withdrawal must be allowed through to the backend. This proves both the
executor's per-tenant withdrawal enforcement (#231) and that the caller identity
bridged onto the streamable-HTTP tool-call path (#387) reaches the enforcement
point -- previously provable only in-process.

The module-local fixture reuses the shared ``_serve_hangar`` engine from
``conftest.py`` and the ``X-API-Key`` per-tenant seeding from ``_group_support``.
Skip-safe: missing binary / stub / unhealthy startup skip rather than fail.
See ``docs/internal/LIVE_VERIFICATION.md``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator, Iterator
from pathlib import Path
import sys
from typing import Any, cast

import pytest

from tests.live import _group_support as gs
from tests.live.conftest import _MATH_SERVER, _hangar_bin, _serve_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

# The stub math backend exposes an anonymous ``power`` tool; we withdraw it for
# TENANT_A only. TENANT_B keeps it. Each tenant carries identity over X-API-Key.
TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
WITHDRAWN_TOOL = "power"
ALLOWED_TOOL = "add"

# Auth is required so the two callers can be told apart by tenant. Because auth is
# on, the #389 tool:invoke authorization gate is live, so each tenant principal
# (seeded as ``svc:<tenant>``) is granted the built-in ``developer`` role (which
# carries ``tool:invoke``); otherwise every call would be denied before reaching
# the withdrawal check. This isolates the withdrawal enforcement under test.
_WITHDRAWAL_CONFIG = """\
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
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
    tool_projection:
      tenant_overrides:
        "{tenant_a}":
          withdrawn: [{withdrawn_tool}]
"""


def _extract_batch(payload: Any) -> dict[str, Any]:
    """Pull the hangar_call batch dict (has a ``results`` list) out of an MCP result."""
    for candidate in (payload, isinstance(payload, dict) and payload.get("result")):
        if isinstance(candidate, dict) and isinstance(candidate.get("results"), list):
            return candidate
    raise AssertionError(f"no batch result in payload: {payload!r}")


def _hangar_call(base_url: str, api_key: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke a single-tool ``hangar_call`` over HTTP as ``api_key``; return result[0].

    Carries tenant identity on the shipped ``X-API-Key`` header (authenticates as
    the tenant the key was seeded for). Returns the first call's result dict
    (``success`` / ``error_type`` / ``result``).
    """
    from mcp import ClientSession
    from tests.live._mcp_client import open_mcp_streams

    headers = {"X-API-Key": api_key}

    async def _call() -> dict[str, Any]:
        async with open_mcp_streams(f"{base_url}/mcp", headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "hangar_call",
                    {"calls": [{"mcp_server": "math", "tool": tool, "arguments": arguments}]},
                )
                data = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
                if not (isinstance(data, dict) and (data.get("results") or data.get("result"))):
                    text = "".join(getattr(c, "text", "") for c in result.content if getattr(c, "type", None) == "text")
                    data = json.loads(text) if text else {}
                batch = _extract_batch(data)
                result0: dict[str, Any] = batch["results"][0]
                return result0

    return asyncio.run(_call())


@pytest.fixture(scope="module")
def withdrawal_hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, dict[str, str]]]:
    """Start hangar with ``power`` withdrawn for TENANT_A; yield (base_url, tenant_keys).

    Skips cleanly if the binary or math stub is missing, or the server does not
    become healthy within the startup budget.
    """
    _hangar_bin()  # skip if `mcp-hangar` is not on PATH
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    workdir = tmp_path_factory.mktemp("live_withdrawal")
    auth_db = workdir / "auth.db"
    try:
        tenant_keys = gs.seed_tenant_keys(auth_db, [TENANT_A, TENANT_B])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")

    config_text = _WITHDRAWAL_CONFIG.format(
        auth_db=str(auth_db),
        tenant_a=TENANT_A,
        tenant_b=TENANT_B,
        withdrawn_tool=WITHDRAWN_TOOL,
        python=sys.executable,
        server=str(Path(_MATH_SERVER)),
    )
    # _serve_hangar is a generator (start server -> yield base_url -> teardown);
    # drive it manually so this fixture can package the base URL with tenant keys.
    server = cast(Generator[str, None, None], _serve_hangar(workdir, config_text))
    base_url = next(server)
    try:
        yield base_url, tenant_keys
    finally:
        server.close()


def test_withdrawn_tool_rejected_for_its_tenant_allowed_for_another(withdrawal_hangar):
    """Claim: a tool withdrawn for tenant A is rejected on A's ``hangar_call`` path
    (``CallResult(success=False)``), while a tenant with no withdrawal is allowed.

    The two callers differ only in the identity carried on X-API-Key, so a
    differing outcome proves the withdrawn tenant's identity reached the executor's
    per-tenant withdrawal check (#231 enforcement, #387 identity bridge).
    """
    base_url, keys = withdrawal_hangar

    # Tenant A: the tool is withdrawn -> rejected, backend never invoked.
    a_result = _hangar_call(base_url, keys[TENANT_A], WITHDRAWN_TOOL, {"base": 2, "exponent": 3})
    assert a_result["success"] is False, a_result
    assert a_result["error_type"] == "ToolWithdrawnError", a_result

    # Tenant B: no withdrawal -> the call reaches the backend and succeeds.
    b_result = _hangar_call(base_url, keys[TENANT_B], WITHDRAWN_TOOL, {"base": 2, "exponent": 3})
    assert b_result["success"] is True, b_result

    # Control: a tool NOT withdrawn for anyone succeeds for tenant A too, proving the
    # rejection above is scoped to the withdrawn tool, not a blanket denial of A.
    a_allowed = _hangar_call(base_url, keys[TENANT_A], ALLOWED_TOOL, {"a": 2, "b": 3})
    assert a_allowed["success"] is True, a_allowed
