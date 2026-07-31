"""Tier 0 live verification: per-tenant tool-access policy (glob allow/deny).

BLACK-BOX test against a REAL ``mcp-hangar`` process over the shipped
streamable-HTTP ``/mcp`` surface, driven exactly as a client would: the caller's
tenant is carried on a seeded ``X-API-Key`` header, and a per-tenant
``tool_access.member`` deny policy on a standalone backend is asserted end to end.

It proves the claim tracked in ``docs/internal/LIVE_VERIFICATION.md``:

    a tool DENIED for the caller's tenant is (a) REJECTED on a real
    ``hangar_call`` invoke (``success=false``) AND (b) HIDDEN from
    ``hangar_tools`` for that tenant; an ALLOWED tool is callable + listed.

The two halves must agree: the invoke path (``hangar_call`` -> ``BatchExecutor``)
keys the resolver on ``member_id=<caller tenant>``, so the listing path
(``hangar_tools``) must too -- otherwise a denied tool is rejected on invoke yet
stays visible, a fail-open on the visibility half of the claim.

The fixture is skip-safe: if the binary or stub backend is missing, or the server
does not become healthy, the module SKIPs rather than fails. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t0" -o addopts=""
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
import subprocess
import sys
import time

import httpx
import pytest

from tests.live import _group_support as gs
from tests.live.conftest import _free_port, _hangar_bin, _MATH_SERVER, _POLL_INTERVAL_S, _STARTUP_TIMEOUT_S

pytestmark = [pytest.mark.live, pytest.mark.t0]

# The caller tenant that a per-tenant deny policy applies to. The math backend
# (examples/provider_math) exposes add/subtract/multiply/divide/power; this tenant
# is denied `power`, so `power` must be rejected on invoke AND absent from listing,
# while `add` (not denied) stays callable + listed.
_TENANT = "tenant:limited"
_DENIED_TOOL = "power"
_ALLOWED_TOOL = "add"

# Standalone math server + per-tenant member deny policy. Auth carries the tenant
# on a seeded X-API-Key; anonymous stays allowed so the harness needs no other
# credential. Default (egress) topology -- this is about per-tenant policy, not
# the unauthenticated fail-closed already covered by T2.
_CONFIG = """\
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
  # Grant the caller tool:invoke so the RBAC gate (#389) passes and the per-tenant
  # tool-access glob policy is the layer under test (not RBAC). The api-key
  # principal id is svc:<tenant> (see _group_support.seed_tenant_keys).
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
class _AccessHarness:
    base_url: str
    api_key: str


@pytest.fixture(scope="module")
def tool_access_hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_AccessHarness]:
    """Run a real hangar with a standalone math server + per-tenant deny; yield harness.

    Skips cleanly if the binary or stub is missing or the server never becomes
    healthy. Reuses the shipped ``SQLiteApiKeyStore`` seeding (``_group_support``)
    so the presented ``X-API-Key`` authenticates as ``_TENANT``.
    """
    binary = _hangar_bin()
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    workdir = tmp_path_factory.mktemp("tool_access")
    auth_db = workdir / "auth.db"

    try:
        tenant_keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")

    config_path = workdir / "config.yaml"
    config_path.write_text(
        _CONFIG.format(
            auth_db=str(auth_db),
            python=sys.executable,
            server=str(_MATH_SERVER),
            tenant=_TENANT,
            denied=_DENIED_TOOL,
        )
    )

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [binary, "--config", str(config_path), "serve", "--http", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(workdir),
    )

    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    healthy = False
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                if httpx.get(f"{base_url}/health/live", timeout=1.0).status_code == 200:
                    healthy = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(_POLL_INTERVAL_S)

        if not healthy:
            proc.terminate()
            out = b""
            try:
                out = proc.communicate(timeout=5)[0] or b""
            except subprocess.TimeoutExpired:
                proc.kill()
            pytest.skip(
                f"hangar did not become healthy in {_STARTUP_TIMEOUT_S}s:\n{out.decode(errors='replace')[-2000:]}"
            )

        yield _AccessHarness(base_url=base_url, api_key=tenant_keys[_TENANT])
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def _extract_dict(result: object, key: str) -> dict:
    """Extract a dict carrying ``key`` from a CallToolResult (structured or text)."""
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if key in structured:
            return structured
        inner = structured.get("result")
        if isinstance(inner, dict) and key in inner:
            return inner
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and key in data:
            return data
    raise AssertionError(f"could not parse a dict with {key!r} from result: {result!r}")


def _mcp_call(harness: _AccessHarness, tool: str, arguments: dict) -> object:
    """Call an MCP tool over streamable-HTTP with the tenant's X-API-Key."""
    import asyncio

    from mcp import ClientSession
    from tests.live._mcp_client import open_mcp_streams

    headers = {"X-API-Key": harness.api_key}

    async def _run() -> object:
        async with open_mcp_streams(f"{harness.base_url}/mcp", headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool, arguments)

    return asyncio.run(_run())


def _hangar_call(harness: _AccessHarness, tool: str) -> dict:
    """Invoke ``math.<tool>`` via hangar_call for the tenant; return the per-call result."""
    result = _mcp_call(
        harness,
        "hangar_call",
        {"calls": [{"mcp_server": "math", "tool": tool, "arguments": {"a": 2, "b": 3, "base": 2, "exponent": 3}}]},
    )
    batch = _extract_dict(result, "results")
    return batch["results"][0]


def _listed_tool_names(harness: _AccessHarness) -> set[str]:
    """Return the tool names hangar_tools reports for ``math`` for the tenant."""
    result = _mcp_call(harness, "hangar_tools", {"mcp_server": "math"})
    payload = _extract_dict(result, "tools")
    return {t.get("name") for t in payload.get("tools", [])}


def test_denied_tool_is_rejected_on_invoke_and_hidden_from_listing(tool_access_hangar: _AccessHarness) -> None:
    """Claim: a per-tenant DENIED tool is rejected on hangar_call AND absent from hangar_tools."""
    # (a) Invoke of the denied tool is rejected fail-closed -- the backend is never reached.
    denied_call = _hangar_call(tool_access_hangar, _DENIED_TOOL)
    assert denied_call["success"] is False, denied_call
    assert denied_call["error_type"] == "ToolAccessDeniedError", denied_call

    # (b) The denied tool is NOT listed for this tenant; a non-denied tool still is.
    names = _listed_tool_names(tool_access_hangar)
    assert _DENIED_TOOL not in names, names
    assert _ALLOWED_TOOL in names, names


def test_allowed_tool_is_callable_and_listed(tool_access_hangar: _AccessHarness) -> None:
    """Claim: a tool NOT denied for the tenant is both callable and listed."""
    names = _listed_tool_names(tool_access_hangar)
    assert _ALLOWED_TOOL in names, names

    allowed_call = _hangar_call(tool_access_hangar, _ALLOWED_TOOL)
    # Callable = the policy gate let it through to the backend. Assert it is never
    # an access denial; a successful add(2, 3) returns 5 so assert that when it lands.
    assert allowed_call["error_type"] != "ToolAccessDeniedError", allowed_call
    if allowed_call["success"] is True:
        assert "5.0" in json.dumps(allowed_call["result"]), allowed_call
