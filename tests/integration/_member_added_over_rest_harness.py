"""Boot Hangar, add a member to a group over the REST API, and read the projection registry (#2258).

Run as a script, in its own interpreter, by
``test_a_member_added_over_rest_is_projected.py``:
``python _member_added_over_rest_harness.py <workdir> <out.json>``. Not
collected by pytest. A separate process because ``bootstrap()`` fills
process-global state.

What runs is production: ``bootstrap(config_path=...)`` in the default egress
topology, where nothing warms the fleet, and the REST API as ``serve --http``
mounts it at ``/api``, under starlette's ``TestClient``, against real stdio
subprocess servers. No worker is started and no MCP call is made, so nothing
but the add can be what projected the member.

- ``solo`` is a server in no group: nothing starts it at load.
- ``pool`` is created over REST with ``auto_start`` (the default), and
  ``POST /api/groups/pool/members`` adds ``solo`` to it, which starts it.
- ``spare`` is a server in no group that nothing adds: it stays cold.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

TESTS = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, str(TESTS / "mock_provider.py")]
BASE_URL = "http://127.0.0.1:8000"


def _server() -> dict[str, Any]:
    return {"mode": "subprocess", "command": COMMAND, "idle_ttl_s": 600}


def _read(repository: Any, ids: list[str]) -> dict[str, Any]:
    from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry

    registry = get_tool_projection_registry()
    out: dict[str, Any] = {}
    for mcp_server_id in ids:
        server = repository.get(mcp_server_id)
        out[mcp_server_id] = {
            "state": server.state.value,
            "projected": sorted(p.tool for p in registry.list_for_server(mcp_server_id)),
            # What a GC sweep or a later command would otherwise publish, late.
            "left_on_the_aggregate": server.has_uncommitted_events(),
        }
    return out


def main(workdir: Path, out: Path) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data

    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import McpServerStarted
    from mcp_hangar.server.api import create_api_router
    from mcp_hangar.server.bootstrap import bootstrap

    config = workdir / "config.yaml"
    servers = {"solo": _server(), "spare": _server()}
    config.write_text(yaml.safe_dump({"logging": {"level": "WARNING"}, "mcp_servers": servers}), encoding="utf-8")
    context = bootstrap(config_path=str(config))
    runtime = context.runtime

    started: list[str] = []

    def observe(event: object) -> None:
        if isinstance(event, McpServerStarted):
            started.append(event.mcp_server_id)

    runtime.event_bus.subscribe(McpServerStarted, observe, kind=HandlerKind.EFFECT)

    api_app = create_api_router(auth_components=getattr(context, "auth_components", None))
    api = TestClient(Starlette(routes=[Mount("/api", app=api_app)]), base_url=BASE_URL)

    report: dict[str, Any] = {"booted": _read(runtime.repository, ["solo", "spare"])}
    created = api.post("/api/groups/", json={"group_id": "pool"})
    added = api.post("/api/groups/pool/members", json={"member_id": "solo"})
    report["responses"] = {
        "create": [created.status_code, created.json()],
        "add": [added.status_code, added.json()],
    }
    # Read as the add returns: nothing else has run in between.
    report["added"] = _read(runtime.repository, ["solo", "spare"])
    report["starts_published_by_the_add"] = sorted(started)

    for server in runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
