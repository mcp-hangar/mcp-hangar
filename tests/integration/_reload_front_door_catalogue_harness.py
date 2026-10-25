"""Boot a front door from a file, add, rebuild and remove servers by reload, and list over HTTP.

Run as a script, in its own interpreter, by
``test_a_server_a_reload_adds_is_served_on_the_front_door.py``:
``python _reload_front_door_catalogue_harness.py <front_door|egress> <workdir> <out.json>``.
Not collected by pytest, for the reason ``_reload_served_harness.py`` gives:
``bootstrap()`` fills process-global state.

What runs is production: ``bootstrap(config_path=...)``, the boot warm-up
``serve`` runs, the app ``serve --http`` serves behind its authentication layer,
the reload through ``POST /api/config/reload`` with an admin key, and every
listing and call over streamable HTTP with a tenant's key. A ``GET /mcp`` stream,
the channel an HTTP client hears ``notifications/tools/list_changed`` on, is
held open across the reload on a real loopback socket.

* boot: ``edit`` only, a real stdio subprocess (``tests/mock_provider.py``) whose
  ``add`` description is ``before``. Its ``echo`` is denied, so no name it lists
  is one ``late`` lists too: a flat listing names tools without their server.
* added: ``late`` (``_reload_upstream.py``: ``echo`` and ``links``) is added, and
  ``edit``'s description becomes ``after``, which rebuilds it.
* removed: ``late`` is deleted again.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import yaml
from _reload_served_harness import BASE_URL, MOCK_PROVIDER, UPSTREAM, _Client, _seed_keys, _served_app

HANDSHAKE = "2025-11-25"
LIST_CHANGED = "notifications/tools/list_changed"
#: Only ``late`` lists it.
LATE_TOOL = "links"


def _edit(description: str) -> dict[str, Any]:
    return {
        "mode": "subprocess",
        "command": [sys.executable, str(MOCK_PROVIDER)],
        "idle_ttl_s": 600,
        "env": {"MOCK_ADD_DESCRIPTION": description},
        "tools": {"deny_list": ["echo"]},
    }


def _config(workdir: Path, mode: str, phase: str) -> dict[str, Any]:
    servers: dict[str, Any] = {"edit": _edit("before" if phase == "boot" else "after")}
    if phase == "added":
        servers["late"] = {"mode": "subprocess", "command": [sys.executable, str(UPSTREAM)], "idle_ttl_s": 600}
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
        "mcp_servers": servers,
    }


class _Stream:
    """One open ``GET /mcp`` over a real socket, collecting its event frames."""

    def __init__(self, base_url: str, key: str) -> None:
        self.frames: queue.Queue[str] = queue.Queue()
        self.status: queue.Queue[int] = queue.Queue()
        self._stop = threading.Event()
        self._client = httpx.Client(timeout=httpx.Timeout(10.0, read=None))
        headers = {"Accept": "text/event-stream", "X-API-Key": key, "MCP-Protocol-Version": HANDSHAKE}
        self._thread = threading.Thread(target=self._read, args=(base_url, headers), daemon=True)
        self._thread.start()
        self.code = self.status.get(timeout=10)

    def _read(self, base_url: str, headers: dict[str, str]) -> None:
        try:
            with self._client.stream("GET", f"{base_url}/mcp", headers=headers) as response:
                self.status.put(response.status_code)
                for chunk in response.iter_text():
                    for frame in chunk.replace("\r\n", "\n").split("\n\n"):
                        if frame.strip() and not frame.startswith(":"):
                            self.frames.put(frame)
                    if self._stop.is_set():
                        return
        except httpx.HTTPError:
            pass
        finally:
            self.status.put(-1)

    def drain(self) -> list[str]:
        out = []
        while True:
            try:
                out.append(self.frames.get_nowait())
            except queue.Empty:
                return out

    def told(self, timeout: float) -> bool:
        """Whether a ``list_changed`` arrives within *timeout*."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if LIST_CHANGED in self.frames.get(timeout=max(0.0, deadline - time.monotonic())):
                    return True
            except queue.Empty:
                return False
        return False

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._client.close()


def _uvicorn(app: Any) -> tuple[Any, threading.Thread, str]:
    import uvicorn

    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    return server, thread, f"http://127.0.0.1:{port}"


class _Door(_Client):
    def tools(self) -> dict[str, str]:
        """The tenant's listing: each tool's name and description."""
        payload = self.rpc("tenant", "tools/list", {})
        return {t["name"]: t.get("description", "") for t in payload["result"]["tools"]}

    def until(self, predicate: Any, timeout: float = 20.0) -> dict[str, str]:
        """The listing once *predicate* holds of it, or the last one at the deadline."""
        deadline = time.monotonic() + timeout
        while True:
            tools = self.tools()
            if predicate(tools) or time.monotonic() > deadline:
                return tools
            time.sleep(0.2)

    def call(self, name: str) -> Any:
        payload = self.rpc("tenant", "tools/call", {"name": name, "arguments": {}})
        if "error" in payload:
            return payload["error"]["code"]
        return "error-result" if payload["result"].get("isError") else "served"


def run(mode: str, workdir: Path) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import ServerLifecycle, warm_the_front_door_catalogue

    keys = _seed_keys(workdir / "auth.db")
    path = workdir / "config.yaml"
    path.write_text(yaml.safe_dump(_config(workdir, mode, "boot")), encoding="utf-8")
    context = bootstrap(config_path=str(path))
    repository = context.runtime.repository
    warm_the_front_door_catalogue(context.runtime)  # what `serve` runs at boot
    out: dict[str, Any] = {}

    def state(mcp_server_id: str) -> str | None:
        server = repository.get(mcp_server_id)
        return None if server is None else server.state.value

    app = _served_app(context, ServerLifecycle(context))
    with TestClient(app, base_url=BASE_URL) as client:
        door = _Door(client, keys)
        server, thread, base_url = _uvicorn(client.app)
        stream = None
        try:
            if mode == "front_door":
                boot = door.until(lambda t: "add" in t)
                stream = _Stream(base_url, keys["tenant"])
                time.sleep(1.0)
                stream.drain()  # the notification every stream gets as it opens
            else:
                boot = door.tools()
            out["boot"] = {"tools": sorted(boot), "add": boot.get("add"), "states": {"edit": state("edit")}}

            path.write_text(yaml.safe_dump(_config(workdir, mode, "added")), encoding="utf-8")
            status, body = door.reload("admin")
            added: dict[str, Any] = {"status": status, "added": body.get("result", {}).get("mcp_servers_added")}
            if mode == "front_door":
                assert stream is not None
                added["told"] = stream.told(timeout=20.0)
                tools = door.until(lambda t: LATE_TOOL in t)
                added["tools"] = sorted(tools)
                added["late_call"] = door.call(LATE_TOOL)
                added["add"] = door.until(lambda t: t.get("add") == "after").get("add")
            else:
                time.sleep(2.0)  # long enough for a start to show, had one been made
                added["tools"] = sorted(door.tools())
            added["states"] = {"edit": state("edit"), "late": state("late")}
            out["added"] = added

            if mode == "front_door":
                path.write_text(yaml.safe_dump(_config(workdir, mode, "removed")), encoding="utf-8")
                status, _body = door.reload("admin")
                # One listing: the projection of a removed server is not dropped
                # (no path removes one yet), so waiting for it to go never ends.
                tools = door.tools()
                out["removed"] = {
                    "status": status,
                    "tools": sorted(tools),
                    "late_call": door.call(LATE_TOOL),
                    "in_repository": repository.exists("late"),
                }
        finally:
            if stream is not None:
                stream.close()
            server.should_exit = True
            thread.join(timeout=10)

    for mcp_server in repository.get_all().values():
        mcp_server.shutdown()
    return out


def main(mode: str, workdir: Path, out: Path) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    out.write_text(json.dumps(run(mode, workdir)))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
