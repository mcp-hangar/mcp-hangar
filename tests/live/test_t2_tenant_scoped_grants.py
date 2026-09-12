"""Tier 2 live verification: a tenant-scoped role grant stays within its tenant.

Black-box against a REAL ``mcp-hangar serve --http``. API-key auth is on with
``allow_anonymous: false``. Keys are seeded into its SQLite store carrying a
tenant, and role assignments come from config: some at ``tenant:A`` or
``tenant:B``, some global. It drives the shipped surfaces the way a client does.

REST:

* a fleet-wide route refuses a tenant-scoped grant and serves a global one;
* the invocation history a tenant-scoped viewer reads names only its tenant;
* a tenant-scoped developer's withdraw stays in its tenant, and the MCP call
  path honours it.

MCP over streamable-HTTP:

* a ``hangar_*`` management tool refuses a tenant-scoped grant and serves a
  global one. This is where identity has been lost before, so it is checked on
  the real transport rather than with a mock context.

``/ws/events``, when a websocket library is installed (uvicorn needs one to
serve the socket at all):

* a tenant-scoped auditor receives only its tenant's events.

Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t2" -o addopts=""
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
import json
import sys
import time

import httpx
import pytest

from tests.live.conftest import _MATH_SERVER, _serve_hangar

pytestmark = [pytest.mark.live, pytest.mark.t2]

_SCOPE_REFUSAL = "tenant-scoped grant; route requires a global grant"

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
    - principal: "svc:viewer-a"
      role: viewer
      scope: "tenant:A"
    - principal: "svc:viewer-global"
      role: viewer
      scope: global
    - principal: "svc:dev-a"
      role: developer
      scope: "tenant:A"
    - principal: "svc:dev-b"
      role: developer
      scope: "tenant:B"
    - principal: "svc:auditor-a"
      role: auditor
      scope: "tenant:A"
    - principal: "svc:auditor-global"
      role: auditor
      scope: global
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    idle_ttl_s: 60
"""

#: principal -> the tenant its key carries (None: no tenant).
_KEY_TENANTS: dict[str, str | None] = {
    "viewer-a": "A",
    "viewer-global": None,
    "dev-a": "A",
    "dev-b": "B",
    "auditor-a": "A",
    "auditor-global": None,
}


@dataclass
class _Harness:
    base_url: str
    keys: dict[str, str]


@pytest.fixture(scope="module")
def tenant_hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    workdir = tmp_path_factory.mktemp("hangar_tenant_grants")
    auth_db = workdir / "auth.db"
    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:
        keys = {
            name: store.create_key(principal_id=f"svc:{name}", name=name, tenant_id=tenant)
            for name, tenant in _KEY_TENANTS.items()
        }
    finally:
        store.close()

    config = _CONFIG.format(auth_db=auth_db, python=sys.executable, server=str(_MATH_SERVER))
    for base_url in _serve_hangar(workdir, config):
        yield _Harness(base_url=base_url, keys=keys)


def _mcp(base_url: str, key: str, tool: str, arguments: dict) -> tuple[bool, str, object]:
    """Call *tool* over streamable-HTTP as *key*: ``(is_error, text, result)``."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    async def _run():
        async with open_mcp_streams(f"{base_url}/mcp", {"X-API-Key": key}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool, arguments)

    try:
        result = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 -- a protocol-level refusal is an outcome here
        return True, str(exc), None
    text = " ".join(getattr(block, "text", "") or "" for block in getattr(result, "content", None) or [])
    return bool(getattr(result, "isError", False)), text, result


def _add(base_url: str, key: str) -> dict:
    """One ``math.add`` through ``hangar_call``; the per-call result dict."""
    _is_error, text, result = _mcp(
        base_url, key, "hangar_call", {"calls": [{"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}]}
    )
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    candidates = [structured, (structured or {}).get("result") if isinstance(structured, dict) else None]
    try:
        candidates.append(json.loads(text))
    except (TypeError, ValueError):
        pass
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("results"):
            return candidate["results"][0]
    raise AssertionError(f"could not read the hangar_call batch: {text!r}")


def _tenant_of(row: dict) -> str | None:
    tenants = {
        t
        for t in (row.get("tenant_id"), (row.get("identity_context") or {}).get("tenant_id"))
        if isinstance(t, str) and t
    }
    return tenants.pop() if len(tenants) == 1 else None


def test_a_fleet_wide_route_refuses_a_tenant_scoped_grant(tenant_hangar: _Harness) -> None:
    url = f"{tenant_hangar.base_url}/api/mcp_servers"
    tenant = httpx.get(url, headers={"X-API-Key": tenant_hangar.keys["viewer-a"]}, follow_redirects=True)
    fleet = httpx.get(url, headers={"X-API-Key": tenant_hangar.keys["viewer-global"]}, follow_redirects=True)

    assert tenant.status_code == 403, tenant.text
    assert tenant.json()["error"]["details"]["reason"] == _SCOPE_REFUSAL
    assert fleet.status_code == 200, fleet.text


def test_a_management_tool_refuses_a_tenant_scoped_grant_over_mcp(tenant_hangar: _Harness) -> None:
    # The refusal travels as the tool result's text; the transport's error flag
    # is not what is being tested, so the text is what is asserted on.
    _, refused_text, _ = _mcp(tenant_hangar.base_url, tenant_hangar.keys["dev-a"], "hangar_list", {})
    _, served_text, _ = _mcp(tenant_hangar.base_url, tenant_hangar.keys["viewer-global"], "hangar_list", {})

    assert "Not authorized to call 'hangar_list'" in refused_text, refused_text
    assert "a global role grant is required" in refused_text, refused_text
    assert "Not authorized" not in served_text, served_text
    assert "math" in served_text, served_text


def test_the_invocation_history_names_only_the_readers_tenant(tenant_hangar: _Harness) -> None:
    for key in ("dev-a", "dev-b"):
        call = _add(tenant_hangar.base_url, tenant_hangar.keys[key])
        assert call["error_type"] != "AuthorizationDenied", call  # hangar_call accepts a tenant grant

    def history(key: str) -> list[dict]:
        response = httpx.get(
            f"{tenant_hangar.base_url}/api/mcp_servers/math/tools/history?limit=500",
            headers={"X-API-Key": tenant_hangar.keys[key]},
        )
        assert response.status_code == 200, response.text
        return response.json()["history"]

    fleet_tenants = {_tenant_of(row) for row in history("viewer-global")}
    assert {"A", "B"} <= fleet_tenants, fleet_tenants  # both calls are recorded, with their tenants

    tenant_rows = history("viewer-a")
    assert tenant_rows, "tenant A's own invocation is missing"
    assert {_tenant_of(row) for row in tenant_rows} == {"A"}


def test_a_tenant_scoped_withdrawal_stays_in_its_tenant_on_the_call_path(tenant_hangar: _Harness) -> None:
    url = f"{tenant_hangar.base_url}/api/admin/tools/math/add"
    dev_a = {"X-API-Key": tenant_hangar.keys["dev-a"]}

    other = httpx.post(f"{url}/withdraw", headers=dev_a, json={"tenant_id": "B"})
    assert other.status_code == 403, other.text

    own = httpx.post(f"{url}/withdraw", headers=dev_a, json={})
    assert own.status_code == 200, own.text
    assert own.json()["tenant_id"] == "A"
    try:
        # The MCP surface reads the same registry with the caller's tenant.
        a_call = _add(tenant_hangar.base_url, tenant_hangar.keys["dev-a"])
        b_call = _add(tenant_hangar.base_url, tenant_hangar.keys["dev-b"])
        assert a_call["success"] is False and "withdrawn" in json.dumps(a_call).lower(), a_call
        assert "withdrawn" not in json.dumps(b_call).lower(), b_call
    finally:
        restored = httpx.post(f"{url}/restore", headers=dev_a, json={})
        assert restored.status_code == 200, restored.text


def _events_seen(base_url: str, key: str, trigger: list[str], keys: dict[str, str], want_tenant: str) -> list[dict]:
    """Subscribe as *key*, make one call per *trigger* principal, collect events until *want_tenant*'s completion."""
    ws_client = pytest.importorskip(
        "websockets.sync.client", reason="websocket client (and server support) not installed"
    )

    received: list[dict] = []
    with ws_client.connect(
        base_url.replace("http://", "ws://") + "/api/ws/events", additional_headers={"X-API-Key": key}, open_timeout=10
    ) as ws:
        ws.send(json.dumps({"type": "subscribe"}))
        assert json.loads(ws.recv(timeout=10))["type"] == "subscribed"
        time.sleep(0.5)  # the bus subscription follows the ack
        for name in trigger:
            _add(base_url, keys[name])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                message = json.loads(ws.recv(timeout=max(0.1, deadline - time.monotonic())))
            except TimeoutError:
                break
            if "event_type" not in message:
                continue
            received.append(message)
            if message["event_type"] == "ToolInvocationCompleted" and _tenant_of(message) == want_tenant:
                break
    return received


def test_a_tenant_scoped_auditor_receives_only_its_tenant_on_the_socket(tenant_hangar: _Harness) -> None:
    seen = _events_seen(
        tenant_hangar.base_url, tenant_hangar.keys["auditor-a"], ["dev-b", "dev-a"], tenant_hangar.keys, "A"
    )

    assert any(m["event_type"] == "ToolInvocationCompleted" for m in seen), seen
    assert {_tenant_of(m) for m in seen} == {"A"}, [(m["event_type"], _tenant_of(m)) for m in seen]


def test_a_global_auditor_still_receives_every_tenant_on_the_socket(tenant_hangar: _Harness) -> None:
    seen = _events_seen(
        tenant_hangar.base_url, tenant_hangar.keys["auditor-global"], ["dev-b", "dev-a"], tenant_hangar.keys, "A"
    )

    assert {"A", "B"} <= {_tenant_of(m) for m in seen}, [(m["event_type"], _tenant_of(m)) for m in seen]
