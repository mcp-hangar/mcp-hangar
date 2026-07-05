"""Tier 0 live verification: digest pinning on the real ``hangar_call`` path.

Supply-chain integrity claim (LIVE_VERIFICATION.md / #276/#280): when a tenant
has pinned a tool to an approved SHA-256 digest, a tool whose *current* schema no
longer matches that digest -- a drifted or tampered tool -- MUST be blocked over
the shipped streamable-HTTP ``hangar_call`` surface, not merely in unit tests.

Prior live verification found FOUR fail-open bugs (auth, RBAC, identity
propagation, tool:invoke) where a control was unit-tested but silently defaulted
over the real HTTP tool surface. Digest pinning depends on the SAME two inputs
those gaps hinged on: the caller's tenant identity (needed to resolve the
per-tenant pin -- ``resolve_pin`` returns ``None`` for an unknown tenant, which
would skip the check) and the tool's discovered projection schema. This test
drives a real hangar + the shipped ``provider_identity`` stub over ``hangar_call``
and asserts the check actually fires end to end. The caller is a real
API-key-authenticated tenant granted ``tool:invoke`` (the #386/#387 RBAC gate)
so the request reaches the executor where digest enforcement lives.

Design (single stub, per-tenant pins; a stale pin == a drifted-from-approved
tool -- the enforcement branch and rejection are identical to a backend that
serves a mutated schema):

* ``echo``   pinned to a stale digest that cannot match its real schema
             -> MUST be rejected (``success=False``,
             ``error_type == "ToolDigestMismatchError"``), never dispatched.
* ``whoami`` pinned to its real (matching) digest -> MUST be allowed.

Skip-safe: skips (never fails) when the CLI, the stub, a free port, the SQLite
key store, or a healthy/warmed server are unavailable. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m live -o addopts=""
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Generator, Iterator
import sys
from typing import cast

import pytest

from tests.live import _group_support as gs
from tests.live.conftest import _serve_hangar  # reuse the shared "real hangar over HTTP" engine

pytestmark = [pytest.mark.live, pytest.mark.t0]

#: Identity stub -- speaks stdio by default (the transport ``mode: subprocess`` uses).
_STUB = gs.IDENTITY_SERVER
_STUB_IDENTITY = "solo"

_SERVER = "identity"
#: Tenant whose API key carries identity and owns the pins under test.
_TENANT = "tenant:digest"
#: The stored principal for that tenant's API key (see ``gs.seed_tenant_keys``).
_PRINCIPAL = f"svc:{_TENANT}"
#: A valid 64-hex digest that cannot match ``echo``'s real schema -> drift/tamper.
_STALE_DIGEST = "b" * 64

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
  role_assignments:
    - principal: "{principal}"
      role: developer
      scope: global
mcp_servers:
  {server}:
    mode: subprocess
    command: ["{python}", "{stub}", "{identity}"]
    idle_ttl_s: 120
    tool_projection:
      digest_enforcement: block
      tenant_overrides:
        "{tenant}":
          pins:
            echo: "{stale}"
            whoami: "{whoami_digest}"
"""


def _live_tool_digests() -> dict[str, str]:
    """Return ``{tool_name: sha256}`` computed the way hangar computes them.

    Reads the stub's real ``tools/list`` over stdio and canonicalises each tool
    with the very same :func:`compute_tool_digest` the projection registry uses.
    Hangar subprocess-spawns identical ``server.py`` code, so the schemas -- and
    therefore the SEP-1766 (RFC 8785 JCS) digests -- are identical. The stub's
    identity argument affects only its server name, never its tool schemas.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from mcp_hangar.domain.services.digest_computation import compute_tool_digest

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(_STUB), _STUB_IDENTITY],
        env={**os.environ, "MCP_TRANSPORT": "stdio"},
    )

    async def _list() -> dict[str, str]:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                return {
                    t.name: compute_tool_digest(t.model_dump(by_alias=True, exclude_none=True)).sha256 for t in tools
                }

    return asyncio.run(_list())


def _first_call_result(base_url: str, api_key: str, tool: str, arguments: dict | None = None) -> dict:
    """Invoke ``tool`` on the identity server via ``hangar_call``; return result[0].

    Drives the real streamable-HTTP MCP surface exactly as an agent would, with
    the tenant carried on the shipped ``X-API-Key`` header, and unwraps the first
    per-call result dict (``{success, error_type, ...}``) from the batch envelope.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def _call() -> dict:
        async with streamablehttp_client(f"{base_url}/mcp", headers={"X-API-Key": api_key}) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(
                    "hangar_call",
                    {"calls": [{"mcp_server": _SERVER, "tool": tool, "arguments": arguments or {}}]},
                )
                return _unwrap_batch(res)

    envelope = asyncio.run(_call())
    results = envelope.get("results")
    assert isinstance(results, list) and results, f"no per-call results in hangar_call envelope: {envelope}"
    return cast(dict, results[0])


def _unwrap_batch(call_tool_result: object) -> dict:
    """Extract the ``hangar_call`` batch dict from a CallToolResult.

    Prefers ``structuredContent`` and falls back to scanning text content for the
    JSON envelope, so the test is resilient to how FastMCP serialises the return.
    """
    data = getattr(call_tool_result, "structuredContent", None)
    if isinstance(data, dict) and "results" in data:
        return data
    for chunk in getattr(call_tool_result, "content", []) or []:
        text = getattr(chunk, "text", None)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "results" in parsed:
            return parsed
    raise AssertionError(f"could not find batch results in hangar_call response: {call_tool_result!r}")


@pytest.fixture(scope="module")
def digest_hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    """Start a real hangar with per-tenant digest pins; yield ``(base_url, api_key)``.

    Skips cleanly when any prerequisite is missing.
    """
    if not _STUB.exists():
        pytest.skip(f"identity stub backend not found at {_STUB}")

    try:
        digests = _live_tool_digests()
    except Exception as exc:  # noqa: BLE001 -- prerequisite: skip, never fail
        pytest.skip(f"could not compute stub tool digests over stdio: {exc}")

    if "echo" not in digests or "whoami" not in digests:
        pytest.skip(f"stub does not expose the expected tools (got {sorted(digests)})")

    workdir = tmp_path_factory.mktemp("live_digest")
    auth_db = workdir / "auth.db"
    try:
        tenant_keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    except Exception as exc:  # noqa: BLE001 -- prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API key: {exc}")
    api_key = tenant_keys[_TENANT]

    config_text = _CONFIG.format(
        auth_db=str(auth_db),
        principal=_PRINCIPAL,
        server=_SERVER,
        python=sys.executable,
        stub=str(_STUB),
        identity=_STUB_IDENTITY,
        tenant=_TENANT,
        stale=_STALE_DIGEST,
        whoami_digest=digests["whoami"],
    )

    gen = cast(Generator[str, None, None], _serve_hangar(workdir, config_text))
    base_url = next(gen)  # may pytest.skip if the binary is missing or startup times out
    try:
        # Warm the cold subprocess so the ToolProjectionRegistry is populated: the
        # digest check reads the discovered projection and is a no-op on the very
        # first (cold-start) call, because the projection is built synchronously
        # DURING that call. `whoami` succeeds on the cold-start call (digest check
        # skipped) and populates the projection for the assertions below.
        warmed = False
        for _ in range(40):
            try:
                res = _first_call_result(base_url, api_key, "whoami")
            except Exception:  # noqa: BLE001 -- transient during cold start
                res = {}
            if res.get("success"):
                warmed = True
                break
        if not warmed:
            pytest.skip("identity backend never warmed; projection not populated")
        yield base_url, api_key
    finally:
        gen.close()  # triggers _serve_hangar's teardown (process terminate)


def test_drifted_pinned_tool_is_blocked(digest_hangar: tuple[str, str]) -> None:
    """Claim: a pinned tool whose schema does not match its digest is rejected.

    This is the core supply-chain assertion -- a drifted/tampered tool must be
    BLOCKED over the real ``hangar_call`` path, never dispatched to the backend.
    """
    base_url, api_key = digest_hangar
    result = _first_call_result(base_url, api_key, "echo", {"text": "hello"})

    assert result["success"] is False, f"drifted pinned tool was NOT blocked (fail-open): {result}"
    assert result["error_type"] == "ToolDigestMismatchError", result
    # Never executed -> no backend result leaked back.
    assert result.get("result") is None, result


def test_matching_pinned_tool_is_allowed(digest_hangar: tuple[str, str]) -> None:
    """Claim: a pinned tool whose schema matches its digest executes normally.

    Proves the gate does not over-block: with the projection now populated the
    digest check fires for ``whoami`` and, matching its pin, permits execution.
    """
    base_url, api_key = digest_hangar
    result = _first_call_result(base_url, api_key, "whoami")

    assert result["success"] is True, f"matching pinned tool was wrongly blocked: {result}"
    assert result["error_type"] is None, result
