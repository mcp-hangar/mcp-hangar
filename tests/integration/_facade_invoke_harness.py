"""Make the same calls through `Hangar.invoke` and through `hangar_call`, on one real boot (#1453).

Run as a script, in its own interpreter, by
``test_the_facade_invoke_is_governed_like_hangar_call.py``:
``python _facade_invoke_harness.py <out.json>``. Not collected by pytest.

A separate process because ``bootstrap()`` fills process-global state -- the
runtime singleton, the executor's validator pipeline, the tool-access resolver,
the tenant budgets -- and two boots in one interpreter would read each other's.

``Hangar.from_config`` boots the real ``bootstrap()`` from a file that denies
one tool, withdraws another, caps the payload with a validator, and gives two
tenants an execution budget. Each case is then called twice, by the same
caller: through ``Hangar.invoke``, and as ``hangar_call`` through the app
``serve --http`` serves (``mcp_app_for_serving``), behind API-key
authentication that names the caller and its tenant. A caller with no key is
let through as anonymous, as an unauthenticated ``hangar_call`` is. The
upstream is the in-process HTTP MCP server from ``_front_door_harness``.
Nothing is stubbed.

Naming: neutral placeholders only (store, read_item, write_item, retired_item,
tenant:a, tenant:b).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from typing import Any

import yaml

BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "facade-invoke-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
HEADERS = {
    "MCP-Protocol-Version": MODERN_VERSION,
    "Mcp-Method": "tools/call",
    "Mcp-Name": "hangar_call",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

SERVER = "store"
READ = "read_item"
WRITE = "write_item"
RETIRED = "retired_item"
TENANT_A = "tenant:a"
TENANT_B = "tenant:b"
#: The principal id each tenant's caller has, on both surfaces.
AGENTS = {TENANT_A: "agent-a", TENANT_B: "agent-b"}
#: The validator's cap. A call with one short argument is far under it.
MAX_BYTES = 256

#: name -> (the caller's tenant, None for an anonymous caller; tool; arguments).
#: Run in this order, each through `Hangar.invoke` first and `hangar_call` second.
CASES: dict[str, tuple[str | None, str, dict[str, Any]]] = {
    "allowed": (TENANT_A, READ, {"x": "1"}),
    "denied": (TENANT_A, WRITE, {"x": "1"}),
    "withdrawn": (TENANT_A, RETIRED, {"x": "1"}),
    "validator": (TENANT_A, READ, {"x": "x" * 2048}),
    # Its one call was spent before the cases run.
    "over_budget": (TENANT_B, READ, {"x": "1"}),
    # The budgets have no "*" entry, so a caller with no tenant has none.
    "anonymous": (None, READ, {"x": "1"}),
}


def _config(endpoint: str) -> dict[str, Any]:
    return {
        "mcp_servers": {
            SERVER: {
                "mode": "remote",
                "endpoint": endpoint,
                "tools": {"deny_list": [WRITE]},
                "tool_projection": {"withdrawn": [RETIRED]},
            }
        },
        "interceptors": {"validators": [{"type": "payload_size", "max_bytes": MAX_BYTES}]},
        "execution": {
            "tenant_limits": {
                TENANT_A: {"max_concurrency": 4, "rps": 1000, "burst": 1000},
                # One call, then a refill too slow to start a second.
                TENANT_B: {"max_concurrency": 4, "rps": 0.001, "burst": 1},
            }
        },
        "config_reload": {"enabled": False},
    }


def _principal(tenant: str | None) -> Any:
    from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType

    if tenant is None:
        return None
    return Principal(id=PrincipalId(AGENTS[tenant]), type=PrincipalType.SERVICE_ACCOUNT, tenant_id=tenant)


async def _invoke(hangar: Any, tenant: str | None, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """What `Hangar.invoke` returned or raised."""
    from mcp_hangar.domain.exceptions import MCPError

    try:
        result = await hangar.invoke(SERVER, tool, arguments, principal=_principal(tenant))
    except MCPError as exc:
        return {
            "ok": False,
            "exception": type(exc).__name__,
            "code": getattr(exc, "code", None),
            "message": exc.message,
        }
    return {"ok": True, "result": result}


def _hangar_call(client: Any, key: str | None, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The one call result `hangar_call` returned, over the served app."""
    from _front_door_harness import jsonrpc

    params = {
        "name": "hangar_call",
        "arguments": {"calls": [{"mcp_server": SERVER, "tool": tool, "arguments": arguments}]},
        "_meta": dict(ENVELOPE),
    }
    headers = {**HEADERS, **({"X-API-Key": key} if key else {})}
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    response = client.post("/mcp", headers=headers, content=body)
    response.raise_for_status()
    (call,) = json.loads(jsonrpc(response)["result"]["content"][0]["text"])["results"]
    return dict(call)


async def _run(out: Path) -> dict[str, Any]:
    from http.server import ThreadingHTTPServer

    from _front_door_harness import Upstream
    from starlette.testclient import TestClient

    from mcp_hangar.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator, InMemoryApiKeyStore
    from mcp_hangar.auth.infrastructure.middleware import AuthenticationMiddleware
    from mcp_hangar.facade import Hangar
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    handler: Any = type("_FacadeUpstream", (Upstream,), {"tools": (READ, WRITE, RETIRED), "called": [], "holds": {}})
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    config_path = out.parent / "hangar.yaml"
    endpoint = f"http://127.0.0.1:{upstream.server_address[1]}/mcp"
    config_path.write_text(yaml.safe_dump(_config(endpoint)), encoding="utf-8")

    hangar = Hangar.from_config(config_path)
    await hangar.start()
    context = hangar._context
    assert context is not None

    store = InMemoryApiKeyStore()
    keys = {
        tenant: store.create_key(principal_id=agent, name=f"key-{agent}", tenant_id=tenant)
        for tenant, agent in AGENTS.items()
    }
    authn = AuthenticationMiddleware([ApiKeyAuthenticator(store)], allow_anonymous=True)
    app = create_auth_enforced_app(mcp_app_for_serving(context.mcp_server), SimpleNamespace(authn_middleware=authn))

    cases: dict[str, dict[str, Any]] = {}
    with TestClient(app, base_url=BASE_URL) as client:
        tenant_b_first = await _invoke(hangar, TENANT_B, READ, {"x": "1"})
        for name, (tenant, tool, arguments) in CASES.items():
            cases[name] = {
                "facade": await _invoke(hangar, tenant, tool, arguments),
                "hangar_call": _hangar_call(client, keys.get(tenant) if tenant else None, tool, arguments),
            }

    await hangar.stop()
    upstream.shutdown()
    return {"tenant_b_first": tenant_b_first, "cases": cases, "upstream_called": list(handler.called)}


def main(out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data
    report = asyncio.run(_run(out))
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
