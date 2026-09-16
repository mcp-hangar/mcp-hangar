"""Boot Hangar from a file, reload it, and read each server's log through the API (#1502).

Run as a script, in its own interpreter, by
``test_a_server_a_reload_adds_has_its_log.py``:
``python _added_server_logs_harness.py <workdir> <out.json>``. Not collected by
pytest. A separate process for the reason ``_reload_served_harness.py`` gives:
``bootstrap()`` fills process-global state.

What runs is production: ``bootstrap(config_path=...)``, the app ``serve --http``
serves, the reload goes through ``POST /api/config/reload``, the calls through
``hangar_call`` over streamable HTTP, and the logs are read back through
``GET /api/mcp_servers/{id}/logs`` -- the API that serves a server's output.
Every server is a real stdio subprocess, and each writes one line of its own to
stderr as it starts (``MOCK_STDERR_BANNER``); the gateway's stderr reader is
what puts that line in the buffer.

No configuration file declares a log buffer -- there is no key for one -- so a
server has one only because something attached it. The file is booted, every
server is called once so its process starts and writes, and then one reload:

* ``keep`` is unchanged, so the reload keeps the running object (#1470);
* ``edit`` gets a new banner in its ``env``, so the reload rebuilds it;
* ``drop`` is deleted;
* ``late`` is added, and had no buffer at all before #1502.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from _reload_keeps_harness import _Gateway, _server
from _reload_served_harness import _served_app, _write, BASE_URL

DIFF_KEYS = ("mcp_servers_added", "mcp_servers_removed", "mcp_servers_updated", "mcp_servers_unchanged")

#: What each server writes to its own stderr at startup, and the only thing
#: that reaches a log buffer here. One per server and per process, so a line
#: names both the server it came from and the run that produced it.
BANNERS = {
    "keep": "keep is up",
    "edit": "edit is up before",
    "edit_after": "edit is up after",
    "drop": "drop is up",
    "late": "late is up",
}


def _upstream(banner: str) -> dict[str, Any]:
    return _server(env={"MOCK_STDERR_BANNER": banner})


def _config(phase: str) -> dict[str, Any]:
    boot = phase == "boot"
    return {
        "config_reload": {"enabled": False},
        "mcp_servers": {
            "keep": _upstream(BANNERS["keep"]),
            # The banner is both the `env` change that makes the reload rebuild
            # this one and the line its new process writes.
            "edit": _upstream(BANNERS["edit"] if boot else BANNERS["edit_after"]),
            **({"drop": _upstream(BANNERS["drop"])} if boot else {"late": _upstream(BANNERS["late"])}),
        },
    }


class _LogGateway(_Gateway):
    def logs(self, mcp_server: str) -> Any:
        """That server's log lines, or the status code when the API serves none."""
        response = self.client.get(f"/api/mcp_servers/{mcp_server}/logs")
        if response.status_code != 200:
            return response.status_code
        return [line["content"] for line in response.json()["logs"]]

    def logs_holding(self, mcp_server: str, banner: str) -> Any:
        """The log lines once *banner* is among them: the reader thread is asynchronous.

        Returns what it has at the deadline, so a line that never arrives fails
        the assertion that named it rather than the harness.
        """
        deadline = time.monotonic() + 20
        while True:
            lines = self.logs(mcp_server)
            if not isinstance(lines, list) or banner in lines or time.monotonic() > deadline:
                return lines
            time.sleep(0.1)

    def started(self, mcp_server: str, banner: str) -> Any:
        """Call *mcp_server*, which starts its process, and return the log its banner reached."""
        self.call(mcp_server)
        return self.logs_holding(mcp_server, banner)


def run(workdir: Path) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.infrastructure.persistence.log_buffer import get_log_buffer
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import ServerLifecycle

    path = workdir / "config.yaml"
    _write(path, _config("boot"))
    context = bootstrap(config_path=str(path))
    repository = context.runtime.repository
    out: dict[str, Any] = {}

    def buffer_of(mcp_server_id: str) -> Any:
        server = repository.get(mcp_server_id)
        return None if server is None else server._log_buffer

    def wired(mcp_server_id: str) -> bool:
        """Whether the server fills the very buffer the logs API reads for that id."""
        attached = buffer_of(mcp_server_id)
        return attached is not None and get_log_buffer(mcp_server_id) is attached

    with TestClient(_served_app(context, ServerLifecycle(context)), base_url=BASE_URL) as client:
        gateway = _LogGateway(client, {})
        booted = ("keep", "edit", "drop")
        out["boot"] = {
            "logs": {sid: gateway.started(sid, BANNERS[sid]) for sid in booted},
            "wired": {sid: wired(sid) for sid in booted},
        }
        buffers = {sid: buffer_of(sid) for sid in booted}

        _write(path, _config("edited"))
        status, body = gateway.reload(None)
        out["edited"] = {
            "status": status,
            "diff": {key: body["result"].get(key) for key in DIFF_KEYS},
            # `late` and `edit` are called here, which starts a process that
            # writes its banner; `keep` was never stopped, so it still holds
            # the one it wrote at boot.
            "logs": {
                "keep": gateway.logs("keep"),
                "edit": gateway.started("edit", BANNERS["edit_after"]),
                "late": gateway.started("late", BANNERS["late"]),
                "drop": gateway.logs("drop"),
            },
            "wired": {sid: wired(sid) for sid in ("keep", "edit", "late")},
            "same_buffer_as_boot": {sid: buffer_of(sid) is buffers[sid] for sid in ("keep", "edit")},
            "drop_registered": get_log_buffer("drop") is not None,
            "drop_in_repository": repository.exists("drop"),
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
