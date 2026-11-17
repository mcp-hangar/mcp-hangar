"""Boot Hangar, start and stop a group through the served MCP tools, and read the projection registry (#2265).

Run as a script, in its own interpreter, by
``test_hangar_start_on_a_group_is_projected.py``:
``python _group_start_over_mcp_harness.py <workdir> <out.json>``. Not collected
by pytest. A separate process because ``bootstrap()`` fills process-global state.

What runs is production: ``bootstrap(config_path=...)`` in the default egress
topology, where nothing warms the fleet, and the tools of
``build_serving_mcp_server()`` called through the MCP server's own dispatch,
against real stdio subprocess servers. ``pool`` has ``auto_start`` off, so
nothing starts its members at load; only ``hangar_start`` can have projected them.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

TESTS = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, str(TESTS / "mock_provider.py")]
MEMBERS = ["alpha", "beta"]


def _server() -> dict[str, Any]:
    return {"mode": "subprocess", "command": COMMAND, "idle_ttl_s": 600}


def _read(repository: Any) -> dict[str, Any]:
    from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry

    registry = get_tool_projection_registry()
    out: dict[str, Any] = {}
    for mcp_server_id in MEMBERS:
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

    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import McpServerStarted, McpServerStopped
    from mcp_hangar.server.bootstrap import bootstrap, build_serving_mcp_server
    from mcp_hangar.server.context import get_context

    config = workdir / "config.yaml"
    servers = {
        **{name: _server() for name in MEMBERS},
        "pool": {"mode": "group", "auto_start": False, "members": [{"id": name} for name in MEMBERS]},
    }
    config.write_text(yaml.safe_dump({"logging": {"level": "WARNING"}, "mcp_servers": servers}), encoding="utf-8")
    context = bootstrap(config_path=str(config))
    runtime = context.runtime

    started: list[str] = []
    stopped: list[str] = []

    def observe(event: object) -> None:
        if isinstance(event, McpServerStarted):
            started.append(event.mcp_server_id)
        elif isinstance(event, McpServerStopped):
            stopped.append(event.mcp_server_id)

    for kind in (McpServerStarted, McpServerStopped):
        runtime.event_bus.subscribe(kind, observe, kind=HandlerKind.EFFECT)

    served = build_serving_mcp_server()
    group = get_context().get_group("pool")

    def tool(name: str) -> Any:
        return asyncio.run(served.call_tool(name, {"mcp_server": "pool"}))

    report: dict[str, Any] = {"booted": _read(runtime.repository)}
    tool("hangar_start")
    # Read as the tool returns: nothing else has run in between.
    report["started"] = _read(runtime.repository)
    report["starts_published"] = sorted(started)
    report["group_left_on_the_aggregate"] = group.has_uncommitted_events()
    tool("hangar_stop")
    report["stopped"] = _read(runtime.repository)
    report["stops_published"] = sorted(stopped)

    for server in runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
