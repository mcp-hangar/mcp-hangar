"""Bootstrap Hangar, then call a group and its members by id through the served app.

Run as a script, in its own interpreter, by
``test_a_group_member_named_directly_is_governed_on_the_served_app.py``:
``python _member_direct_governance_harness.py <mode> <out.json>``. Not collected
by pytest.

It runs in a separate process for the reason ``_group_recovery_harness.py``
gives. ``bootstrap()`` fills process-global state (the runtime, ``GROUPS``, the
resolver, the projection registry), and a second bootstrap in the same
interpreter would inherit it.

What runs is production:

- ``bootstrap()``, with a config dict that declares the group's policy,
  withdrawals and pin in the shape a config file uses;
- the app ``serve --http`` serves, under starlette's ``TestClient``;
- stateless ``tools/call hangar_call`` POSTs to ``tests/mock_provider.py`` over
  stdio;
- in ``approval`` mode, the approval gate ``bootstrap()`` wires, which holds a
  call until its timeout because nobody answers;
- in ``l7`` mode, that same gate, with an approver answering it.

In ``auth`` and ``approval`` modes the app is wrapped in the auth enforcement
``run_http`` applies. Each call presents an API key minted in the bootstrapped
store for a principal holding ``developer``, which grants ``tool:invoke``.

One thing is changed, and it is not on the path under test: ``rate_limit`` is
raised. A run makes several dozen calls in about a second, and the default
burst would refuse the last of them with ``RateLimitExceeded``. It would do so
sooner on a tree where the gates let more calls through to an upstream.

Modes:

- ``open``: auth off, so no caller carries a tenant.
- ``auth``: API-key auth on, with one key for each of two tenants.
- ``approval``: ``auth``, plus a group approval list on ``add`` and a
  ``tenant-a`` approval list on the ungrouped server's ``echo``, both with a
  one-second timeout.
- ``l7``: auth off, plus an L7 egress policy on ``math-a`` -- the member the
  group selects -- whose ``requireApproval`` rule covers ``add`` and ``power``,
  and a thread that answers what the gate raises: ``add`` granted, ``power``
  denied. The policy is set on the running server, as the REST endpoint sets
  it; a config file has no shape for one. A call naming the group used to read
  no policy at all, because a group id is not a server id, so the member's rule
  never reached a human and the member's own check refused the call at invoke
  (#1499).

The report is every call's outcome, ``ok`` or the refusal's ``error_type``,
keyed by tenant, then by the id the call named, then by tool.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"
BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "member-direct-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

GROUP = "math-pool"
#: The group's first choice (priority 1). Its entry in the group spec denies `echo`.
MEMBER = "math-a"
SIBLING = "math-b"
#: A server in no group.
SOLO = "math-solo"
#: `tenant-a` has `power` withdrawn on the group; `tenant-b` does not.
TENANTS = ("tenant-a", "tenant-b")
#: Matches no schema, so the group's pin on `divide` refuses every call that reaches it.
STALE_DIGEST = "a" * 64
#: In `l7` mode, `math-a`'s egress policy routes both of these to a human. The
#: approver grants the first and denies the second, so one run shows both ends.
L7_GRANTED, L7_DENIED = "add", "power"

ARGUMENTS: dict[str, dict[str, Any]] = {
    "add": {"a": 1, "b": 2},
    "multiply": {"a": 2, "b": 3},
    "subtract": {"a": 5, "b": 3},
    "divide": {"a": 6, "b": 3},
    "power": {"base": 2, "exponent": 3},
    "echo": {"message": "hi"},
}
ALL_TOOLS = tuple(ARGUMENTS)

#: mode -> (tenant of the calling key, or None) -> the id each call names -> the tools called on it.
CALLS: dict[str, dict[str | None, dict[str, tuple[str, ...]]]] = {
    "open": {None: dict.fromkeys((GROUP, MEMBER, SIBLING, SOLO), ALL_TOOLS)},
    "auth": dict.fromkeys(TENANTS, dict.fromkeys((GROUP, MEMBER, SOLO), ALL_TOOLS)),
    "approval": {
        TENANTS[0]: {GROUP: ("add",), MEMBER: ("add",), SOLO: ("add", "echo")},
        TENANTS[1]: {SOLO: ("echo",)},
    },
    # `math-b` carries no policy of its own: the control for "the rule read is
    # the selected member's", not "any member's".
    "l7": {
        None: {
            GROUP: (L7_GRANTED, L7_DENIED),
            MEMBER: (L7_GRANTED, L7_DENIED),
            SIBLING: (L7_GRANTED, L7_DENIED),
        }
    },
}


def _config(mode: str) -> dict[str, Any]:
    server = {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]}
    group: dict[str, Any] = {
        "mode": "group",
        "strategy": "priority",
        "min_healthy": 1,
        "tools": {"deny_list": ["multiply"]},
        "tool_projection": {
            "digest_enforcement": "block",
            "pins": {"divide": STALE_DIGEST},
            "withdrawn": ["subtract"],
            "tenant_overrides": {TENANTS[0]: {"withdrawn": ["power"]}},
        },
        "members": [
            {"id": MEMBER, "priority": 1, "tools": {"deny_list": ["echo"]}},
            {"id": SIBLING, "priority": 2},
        ],
    }
    solo: dict[str, Any] = dict(server)
    if mode == "approval":
        group["tools"].update({"approval_list": ["add"], "approval_timeout_seconds": 1})
        solo["tool_access"] = {"member": {TENANTS[0]: {"approval_list": ["echo"], "approval_timeout_seconds": 1}}}
    config: dict[str, Any] = {
        "rate_limit": {"rps": 1000, "burst": 1000},
        "mcp_servers": {MEMBER: dict(server), SIBLING: dict(server), SOLO: solo, GROUP: group},
    }
    if mode in ("auth", "approval"):
        config["auth"] = {
            "enabled": True,
            "allow_anonymous": False,
            "api_key": {"enabled": True, "header_name": "X-API-Key"},
            "storage": {"driver": "memory"},
        }
    return config


def _hangar_call(client: Any, headers: dict[str, str], target: str, tool: str) -> str:
    """One stateless ``hangar_call`` of one tool on *target*: ``ok`` or its ``error_type``."""
    params = {
        "name": "hangar_call",
        "arguments": {"calls": [{"mcp_server": target, "tool": tool, "arguments": ARGUMENTS[tool]}]},
        "_meta": ENVELOPE,
    }
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    response = client.post("/mcp", headers=headers, content=body)
    response.raise_for_status()
    text = response.text.lstrip()
    if not text.startswith("{"):  # SSE framing: take the data line
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    batch = json.loads(json.loads(text)["result"]["content"][0]["text"])
    if "results" not in batch:
        return f"batch:{batch.get('error')}"
    [result] = batch["results"]
    return "ok" if result["success"] else str(result["error_type"])


def _keys(context: Any) -> dict[str, str]:
    """One key per tenant, for a principal holding `developer` (which grants `tool:invoke`)."""
    auth = context.auth_components
    keys = {}
    for tenant in TENANTS:
        principal = f"svc:{tenant}"
        keys[tenant] = auth.api_key_store.create_key(principal_id=principal, name=tenant, tenant_id=tenant)
        auth.role_store.assign_role(principal, "developer")
    return keys


def _answer_approvals(gate: Any) -> None:
    """Be the approver: grant every hold on `L7_GRANTED`, deny every hold on `L7_DENIED`.

    Polls the approval record the way the REST resolve endpoint reads it, off
    the loop the held call waits on -- which is the arrangement the gate is
    built for: its wait watches the record as well as the local hold, so a
    decision made elsewhere lands. Reaches ``_repository`` because the REST
    route does; there is no public listing surface on the service.
    """
    while True:
        try:
            for request in asyncio.run(gate._repository.list_pending()):
                asyncio.run(
                    gate.resolve(
                        request.approval_id,
                        approved=request.tool_name == L7_GRANTED,
                        decided_by="ops@example",
                        reason=None if request.tool_name == L7_GRANTED else "not this one",
                    )
                )
        except Exception:  # noqa: BLE001 -- a harness thread must never take the run down
            pass
        time.sleep(0.05)


def _arm_l7(context: Any) -> None:
    """Give the member the group selects a `requireApproval` rule, and staff the gate.

    The gate is read off the served context, which is where `bootstrap()` wires
    it and where the executor's approval gate looks it up.
    """
    from mcp_hangar.domain.policies.egress_l7 import L7Policy
    from mcp_hangar.server.context import get_context

    context.runtime.repository.get(MEMBER).set_l7_policy(
        L7Policy.from_dict(
            {
                "tools": {"requireApproval": [L7_GRANTED, L7_DENIED]},
                "defaultAction": "Allow",
                "mode": "Enforce",
            }
        )
    )
    gate = get_context().approval_gate
    assert gate is not None, "bootstrap wired no approval gate; nothing would answer"
    threading.Thread(target=_answer_approvals, args=(gate,), daemon=True).start()


def main(mode: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from starlette.testclient import TestClient

    import mcp_hangar
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    config = _config(mode)
    context = bootstrap(config_dict=config)
    if mode == "l7":
        _arm_l7(context)

    headers = {
        "MCP-Protocol-Version": MODERN_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "hangar_call",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    app = mcp_app_for_serving(context.mcp_server)
    keys: dict[str, str] = {}
    if "auth" in config:
        from mcp_hangar.server.api.middleware import create_auth_enforced_app

        keys = _keys(context)
        app = create_auth_enforced_app(app, context.auth_components)  # what `run_http` wraps it in

    outcomes: dict[str, dict[str, dict[str, str]]] = {}
    with TestClient(app, base_url=BASE_URL) as client:
        for tenant, targets in CALLS[mode].items():
            caller = {**headers, "X-API-Key": keys[tenant]} if tenant is not None else headers
            outcomes[tenant or "-"] = {
                target: {tool: _hangar_call(client, caller, target, tool) for tool in tools}
                for target, tools in targets.items()
            }

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps({"hangar": mcp_hangar.__file__, "outcomes": outcomes}))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
