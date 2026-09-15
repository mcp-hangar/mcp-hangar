"""Boot Hangar from a file, reload it, and read which server processes lived through it (#1426).

Run as a script, in its own interpreter, by ``test_a_reload_restarts_only_what_changed.py``:
``python _reload_keeps_harness.py <workdir> <out.json>``. Not collected by pytest.
A separate process for the reason ``_reload_served_harness.py`` gives:
``bootstrap()`` fills process-global state.

What runs is production: ``bootstrap(config_path=...)``, the app ``serve --http``
serves, reloads through ``POST /api/config/reload``, and calls through
``hangar_call`` over streamable HTTP. Every server is a real stdio subprocess,
started by the first call made to it.

The file is booted, then reloaded twice:

* ``edited``: ``keep`` is unchanged except for a ``tools`` deny list on
  ``add``; ``edit`` gets a new ``env``; ``drop`` is deleted; ``late`` is added;
  the group ``pool`` gives its inline member ``m1`` a new weight.
* ``lifted``: the deny list on ``keep`` is deleted again.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from _reload_served_harness import _Client, _served_app, _write, BASE_URL

TESTS = Path(__file__).resolve().parents[1]
MOCK_PROVIDER = TESTS / "mock_provider.py"
DIFF_KEYS = ("mcp_servers_added", "mcp_servers_removed", "mcp_servers_updated", "mcp_servers_unchanged")


def _server(**extra: Any) -> dict[str, Any]:
    # No `resources`: a file that leaves them out is the case #1426 is about.
    return {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)], "idle_ttl_s": 600, **extra}


def _config(phase: str) -> dict[str, Any]:
    servers: dict[str, Any] = {
        "keep": _server(tools={"deny_list": ["add"]}) if phase == "edited" else _server(),
        "edit": _server(env={"MOCK_ADD_DESCRIPTION": "before" if phase == "boot" else "after"}),
    }
    if phase == "boot":
        servers["drop"] = _server()
    else:
        servers["late"] = _server()
    member = {"id": "m1", **_server(), "weight": 1 if phase == "boot" else 2}
    servers["pool"] = {"mode": "group", "auto_start": False, "members": [member]}
    return {"config_reload": {"enabled": False}, "mcp_servers": servers}


class _Gateway(_Client):
    def call(self, mcp_server: str) -> str:
        calls = [{"mcp_server": mcp_server, "tool": "add", "arguments": {"a": 1, "b": 2}}]
        payload = self.rpc(None, "tools/call", {"name": "hangar_call", "arguments": {"calls": calls}})
        body = json.loads(payload["result"]["content"][0]["text"])
        if "results" not in body:  # refused before any call ran, e.g. naming no server
            return "invalid: " + "; ".join(error["message"] for error in body["validation_errors"])
        (result,) = body["results"]
        return "served" if result["success"] else str(result.get("error_type"))


def _pid(server: Any) -> int | None:
    client = server._client
    return client.process.pid if client is not None and client.is_alive() else None


def _exited(process: Any) -> bool:
    deadline = time.monotonic() + 10
    while process.poll() is None:
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def run(workdir: Path) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import ServerLifecycle
    from mcp_hangar.server.state import GROUPS

    path = workdir / "config.yaml"
    _write(path, _config("boot"))
    context = bootstrap(config_path=str(path))
    repository = context.runtime.repository
    out: dict[str, Any] = {}

    def pids() -> dict[str, int | None]:
        return {sid: _pid(server) for sid, server in repository.get_all().items()}

    with TestClient(_served_app(context, ServerLifecycle(context)), base_url=BASE_URL) as client:
        gateway = _Gateway(client, {})
        booted_ids = ("keep", "edit", "drop", "m1")
        out["boot"] = {"calls": {sid: gateway.call(sid) for sid in booted_ids}, "pids": pids()}
        booted = {sid: repository.get(sid) for sid in booted_ids}
        processes = {sid: server._client.process for sid, server in booted.items()}

        _write(path, _config("edited"))
        status, body = gateway.reload(None)
        exited = {
            sid: _exited(process) if sid in ("edit", "drop") else process.poll() is not None
            for sid, process in processes.items()
        }
        member = GROUPS["pool"].get_member("m1")
        out["edited"] = {
            "status": status,
            "diff": {key: body["result"].get(key) for key in DIFF_KEYS},
            "exited": exited,
            "same_object": {sid: repository.get(sid) is server for sid, server in booted.items()},
            "drop_registered": repository.exists("drop"),
            "m1_weight": member.weight if member is not None else None,
            "m1_member_is_the_running_server": member is not None and member.mcp_server is booted["m1"],
            "calls": {sid: gateway.call(sid) for sid in ("keep", "edit", "drop", "late", "m1")},
            # After the calls: the refused call on `keep` started nothing new.
            "pids": pids(),
            "edit_description": repository.get("edit").get_tools_dict()["add"].description,
        }

        _write(path, _config("lifted"))
        status, body = gateway.reload(None)
        out["lifted"] = {
            "status": status,
            "diff": {key: body["result"].get(key) for key in DIFF_KEYS},
            "calls": {"keep": gateway.call("keep")},
            "pids": pids(),
        }

    for server in repository.get_all().values():
        server.shutdown()
    return out


def main(workdir: Path, out: Path) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    out.write_text(json.dumps(run(workdir)))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
