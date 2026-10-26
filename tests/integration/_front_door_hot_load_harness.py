"""Hot-load a server on a front door, then list and call over HTTP as a tenant (#2100).

Run as a script, in its own interpreter, by
``test_a_hot_loaded_server_on_the_front_door.py``:
``python _front_door_hot_load_harness.py <front_door|egress> <workdir> <out.json>``.
Not collected by pytest, for the reason ``_reload_served_harness.py`` gives:
``bootstrap()`` fills process-global state.

What runs is production: ``bootstrap(config_path=...)``, the boot warm-up
``serve`` runs, the app ``serve --http`` serves behind its authentication layer,
``hangar_load`` and ``hangar_unload`` over streamable HTTP with an admin key, and
the tenant's listing and calls with its own key. Only the registry and the
installer are stood in for, as in ``_hot_load_logs_harness.py``: they are the
network. The loaded server is a real stdio subprocess (``tests/mock_provider.py``).

* ``kept`` is declared in the file (``_reload_upstream.py``: ``echo`` and
  ``links``), with ``echo`` denied so no name it lists is one the loaded server
  lists too.
* ``hot-one`` is loaded at runtime; ``add`` is a tool only it has.

The same run in ``egress`` mode is the baseline: nothing about a load there
changes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from _hot_load_logs_harness import _load_handler
from _reload_served_harness import BASE_URL, UPSTREAM, _Client, _seed_keys, _served_app

HOT = "hot-one"
#: Only the loaded server lists it.
HOT_TOOL = "add"
KEPT_TOOL = "links"


def _config(workdir: Path, mode: str) -> dict[str, Any]:
    return {
        "tool_access": {"mode": mode},
        "config_reload": {"enabled": False},
        "auth": {
            "enabled": True,
            "allow_anonymous": False,
            "api_key": {"enabled": True, "header_name": "X-API-Key"},
            "storage": {"driver": "sqlite", "path": str(workdir / "auth.db")},
            "role_assignments": [
                {"principal": "group:svc-callers", "role": "service-account", "scope": "global"},
                {"principal": "group:admins", "role": "admin", "scope": "global"},
            ],
        },
        "mcp_servers": {
            "kept": {
                "mode": "subprocess",
                "command": [sys.executable, str(UPSTREAM)],
                "idle_ttl_s": 600,
                "tools": {"deny_list": ["echo"]},
            }
        },
    }


class _Door(_Client):
    def tool(self, who: str, name: str, arguments: dict[str, Any]) -> Any:
        payload = self.rpc(who, "tools/call", {"name": name, "arguments": arguments})
        if "error" in payload:
            return payload["error"]["code"]
        return payload["result"]

    def management(self, who: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.tool(who, name, arguments)
        assert isinstance(result, dict), f"{name} answered {result!r}"
        return dict(json.loads(result["content"][0]["text"]))

    def listing(self, who: str) -> list[str]:
        return sorted(t["name"] for t in self.rpc(who, "tools/list", {})["result"]["tools"])

    def hangar_call(self, who: str, mcp_server: str, tool: str) -> Any:
        """``served`` when the one call reached the upstream and succeeded, else the JSON-RPC error code."""
        call = {"mcp_server": mcp_server, "tool": tool, "arguments": {"a": 2, "b": 3}}
        result = self.tool(who, "hangar_call", {"calls": [call]})
        if isinstance(result, int):
            return result
        return "served" if result["structuredContent"]["results"][0]["success"] else "error-result"

    def flat_call(self, who: str, name: str) -> Any:
        result = self.tool(who, name, {"a": 2, "b": 3})
        if isinstance(result, int):
            return result
        return "error-result" if result.get("isError") else "served"


def run(mode: str, workdir: Path) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.context import get_context
    from mcp_hangar.server.lifecycle import ServerLifecycle, warm_the_front_door_catalogue
    from mcp_hangar.server.state import get_runtime_mcp_servers

    keys = _seed_keys(workdir / "auth.db")
    path = workdir / "config.yaml"
    path.write_text(yaml.safe_dump(_config(workdir, mode)), encoding="utf-8")
    context = bootstrap(config_path=str(path))
    warm_the_front_door_catalogue(context.runtime)  # what `serve` runs at boot
    get_context().load_mcp_server_handler = _load_handler(context)
    out: dict[str, Any] = {}

    with TestClient(_served_app(context, ServerLifecycle(context)), base_url=BASE_URL) as client:
        door = _Door(client, keys)
        out["boot_listing"] = door.listing("tenant")
        out["loaded"] = door.management("admin", "hangar_load", {"name": HOT})
        out["listing"] = door.listing("tenant")
        out["flat_call"] = door.flat_call("tenant", HOT_TOOL)
        out["kept_call"] = door.flat_call("tenant", KEPT_TOOL)
        # `hangar_call` is the `egress` way to reach a server; the front door
        # lists none of the management surface for it.
        out["hangar_call"] = door.hangar_call("admin", HOT, HOT_TOOL)
        out["loaded_again"] = door.management("admin", "hangar_load", {"name": HOT})
        out["unloaded"] = door.management("admin", "hangar_unload", {"mcp_server": HOT})

    for server in list(context.runtime.repository.get_all().values()):
        server.shutdown()
    for entry in get_runtime_mcp_servers().list_all():
        entry[0].shutdown()
    return out


def main(mode: str, workdir: Path, out: Path) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    out.write_text(json.dumps(run(mode, workdir)))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
