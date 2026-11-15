"""Boot Hangar with groups that start their members, reload, and read the projection registry (#2257).

Run as a script, in its own interpreter, by
``test_a_member_started_at_config_load_is_projected.py``:
``python _members_started_at_load_harness.py <workdir> <out.json>``. Not
collected by pytest. A separate process because ``bootstrap()`` fills
process-global state.

What runs is production: ``bootstrap(config_path=...)`` in the default egress
topology, where nothing warms the fleet, and a reload sent through the command
bus, against real stdio subprocess servers. Nothing is served and no worker is
started, so no call, warm-up or GC sweep can be what projected a member.

- ``eager`` starts its members (``auto_start`` is the default): ``inline``,
  defined in the member entry, and ``named``, declared under ``mcp_servers``.
- ``solo`` is a server in no group: nothing starts it at load.
- The reload adds ``late``, a group that starts its one member ``late-inline``.
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


def _server() -> dict[str, Any]:
    return {"mode": "subprocess", "command": COMMAND, "idle_ttl_s": 600}


def _config(*, reloaded: bool) -> dict[str, Any]:
    servers: dict[str, Any] = {
        "named": _server(),
        "solo": _server(),
        "eager": {"mode": "group", "members": [{"id": "inline", **_server()}, {"id": "named"}]},
    }
    if reloaded:
        servers["late"] = {"mode": "group", "members": [{"id": "late-inline", **_server()}]}
    return {"logging": {"level": "WARNING"}, "mcp_servers": servers}


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

    from mcp_hangar.application.commands.commands import ReloadConfigurationCommand
    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import McpServerStarted
    from mcp_hangar.server.bootstrap import bootstrap

    config = workdir / "config.yaml"
    config.write_text(yaml.safe_dump(_config(reloaded=False)), encoding="utf-8")
    context = bootstrap(config_path=str(config))
    runtime = context.runtime
    report: dict[str, Any] = {"booted": _read(runtime.repository, ["inline", "named", "solo"])}

    started: list[str] = []

    def observe(event: object) -> None:
        if isinstance(event, McpServerStarted):
            started.append(event.mcp_server_id)

    runtime.event_bus.subscribe(McpServerStarted, observe, kind=HandlerKind.EFFECT)
    config.write_text(yaml.safe_dump(_config(reloaded=True)), encoding="utf-8")
    runtime.command_bus.send(ReloadConfigurationCommand(config_path=str(config), requested_by="harness"))
    report["reloaded"] = _read(runtime.repository, ["inline", "named", "solo", "late-inline"])
    report["starts_published_by_the_reload"] = sorted(started)

    for server in runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
