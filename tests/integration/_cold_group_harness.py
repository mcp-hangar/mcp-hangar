"""Bootstrap Hangar, let the GC reap a group's members, then read the group everywhere and call it (#1356).

Run as a script, in its own interpreter, by
``test_a_cold_group_reports_no_healthy_members_and_still_routes.py``:
``python _cold_group_harness.py <out.json>``. Not collected by pytest.

A separate process because ``bootstrap()`` fills process-global state that a
second bootstrap in the same interpreter would inherit.

What runs is production: ``bootstrap()`` with a config dict; the MCP app
``serve --http`` serves, and the REST API it mounts at ``/api``, each under
starlette's ``TestClient``; ``hangar_call`` through the group to
``tests/mock_provider.py`` over stdio; and the GC worker ``bootstrap()``
created, started the way ``ServerLifecycle.start`` starts it. One thing is
changed, and it is not on the path under test: the GC interval, 30s in
production, is lowered to a second before ``bootstrap()`` reads it.

The group is read at three moments: after boot, once the GC has reaped every
member, and after one call through the group. At each, every surface that
reports the group is read with nothing running in between: the GC is stopped
and the health worker never starts, so the members cannot change state
between two reads.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"
BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "cold-group-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

GROUP = "math-pool"
MEMBERS = ("math-a", "math-b")
#: The TTL is a second and the GC runs every second, so both go within about two.
REAP_DEADLINE_S = 8.0


def _config() -> dict[str, Any]:
    server = {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)], "idle_ttl_s": 1}
    servers: dict[str, Any] = {member: dict(server) for member in MEMBERS}
    servers[GROUP] = {
        "mode": "group",
        "strategy": "round_robin",
        "min_healthy": 1,
        "members": [{"id": member} for member in MEMBERS],
    }
    return {"mcp_servers": servers}


def _tool(client: Any, name: str, arguments: dict[str, Any]) -> Any:
    """One stateless ``tools/call`` POST to ``/mcp``; the tool's JSON result."""
    headers = {
        "MCP-Protocol-Version": MODERN_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": name,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    params = {"name": name, "arguments": arguments, "_meta": ENVELOPE}
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    response = client.post("/mcp", headers=headers, content=body)
    response.raise_for_status()
    text = response.text.lstrip()
    if not text.startswith("{"):  # SSE framing: take the data line
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    result = json.loads(text)["result"]
    return json.loads(result["content"][0]["text"])


def _rest(api: Any, route: str) -> Any:
    response = api.get(route)
    response.raise_for_status()
    return response.json()


def _surfaces(client: Any, api: Any) -> dict[str, Any]:
    """The group as every surface that reports it answers, one after the other."""
    return {
        "rest": _rest(api, f"/api/groups/{GROUP}"),
        "rest_list": next(g for g in _rest(api, "/api/groups")["groups"] if g["group_id"] == GROUP),
        "hangar_details": _tool(client, "hangar_details", {"mcp_server": GROUP}),
        "hangar_group_list": next(
            g for g in _tool(client, "hangar_group_list", {})["groups"] if g["group_id"] == GROUP
        ),
        "hangar_list": next(g for g in _tool(client, "hangar_list", {})["groups"] if g["group_id"] == GROUP),
        "hangar_status": next(g for g in _tool(client, "hangar_status", {})["groups"] if g["id"] == GROUP),
        # The only group, so its sums are this group's counts.
        "hangar_health": _tool(client, "hangar_health", {})["groups"],
    }


def main(out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import DomainEvent, McpServerStopped
    from mcp_hangar.server.api import create_api_router
    from mcp_hangar.server.bootstrap import bootstrap, workers
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    workers.GC_WORKER_INTERVAL_SECONDS = 1

    context = bootstrap(config_dict=_config())

    stopped: list[list[str]] = []

    def observe(event: DomainEvent) -> None:
        if isinstance(event, McpServerStopped):
            stopped.append([event.mcp_server_id, event.reason])

    context.runtime.event_bus.subscribe_to_all(observe, kind=HandlerKind.PROJECTION)

    # The REST API as `serve --http` mounts it.
    api_app = create_api_router(auth_components=getattr(context, "auth_components", None))
    api = TestClient(Starlette(routes=[Mount("/api", app=api_app)]), base_url=BASE_URL)

    report: dict[str, Any] = {}
    with TestClient(mcp_app_for_serving(context.mcp_server), base_url=BASE_URL) as client:
        report["booted"] = _surfaces(client, api)

        gc = next(w for w in context.background_workers if getattr(w, "task", None) == "gc")
        gc.start()
        deadline = time.monotonic() + REAP_DEADLINE_S
        while time.monotonic() < deadline and len(stopped) < len(MEMBERS):
            time.sleep(0.05)
        gc.stop()
        report["stops"] = list(stopped)

        report["reaped"] = _surfaces(client, api)
        report["call"] = _tool(
            client,
            "hangar_call",
            {"calls": [{"mcp_server": GROUP, "tool": "add", "arguments": {"a": 1, "b": 2}}]},
        )
        report["called"] = _surfaces(client, api)

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
