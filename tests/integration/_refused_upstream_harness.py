"""Bootstrap Hangar with a remote group member, stop its upstream, and let the health worker run (#1698).

Run as a script, in its own interpreter, by
``test_a_refused_connection_fails_a_remote_servers_health_check.py``:
``python _refused_upstream_harness.py <out.json>``. Not collected by pytest.

What runs is production: ``bootstrap()`` with a config dict; the app
``serve --http`` serves, under starlette's ``TestClient``; ``hangar_call``
through the group to a real HTTP upstream on a loopback port (the
``Upstream`` of ``_front_door_harness``); and the health worker ``bootstrap()``
created. Only the worker's interval is lowered, before ``bootstrap()`` reads it.

The upstream is stopped and its listening socket closed, so the port refuses
connections, as a stopped remote server's does. Stopping the loop alone would
leave the socket bound: the kernel would accept into the backlog and the probe
would time out, which is the path that already counted.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

GROUP = "remote-pool"
MEMBER = "remote-a"
TOOL = "add"
#: Checks the server fails before it is degraded; the group's thresholds match
#: it, as the group hears one HealthCheckFailed per failed check and no more.
FAILURES = 2
#: How long the health worker gets. A refused probe takes about 1.5s with the
#: HTTP client's default retries, and a check lands every second.
DEADLINE_S = 25.0


def _config(endpoint: str) -> dict[str, Any]:
    return {
        "mcp_servers": {
            MEMBER: {"mode": "remote", "endpoint": endpoint, "max_consecutive_failures": FAILURES},
            GROUP: {
                "mode": "group",
                "strategy": "priority",
                "min_healthy": 1,
                "health": {"unhealthy_threshold": FAILURES, "healthy_threshold": 1},
                "circuit_breaker": {"failure_threshold": FAILURES},
                "members": [{"id": MEMBER, "priority": 1}],
            },
        },
        "config_reload": {"enabled": False},
    }


def _refuses(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    except ConnectionRefusedError:
        return True
    return False


def main(out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from _front_door_harness import Upstream
    from _group_recovery_harness import _stop_and_join, _tool, _worker
    from starlette.testclient import TestClient

    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import DomainEvent, HealthCheckFailed
    from mcp_hangar.server.bootstrap import bootstrap, workers
    from mcp_hangar.server.lifecycle import mcp_app_for_serving
    from mcp_hangar.server.state import GROUPS

    workers.HEALTH_CHECK_INTERVAL_SECONDS = 1

    handler: Any = type("_RefusedUpstream", (Upstream,), {"tools": (TOOL,), "called": [], "holds": {}})
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = upstream.server_address[1]
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    context = bootstrap(config_dict=_config(f"http://127.0.0.1:{port}/mcp"))
    failed: list[int] = []

    def observe(event: DomainEvent) -> None:
        if isinstance(event, HealthCheckFailed) and event.mcp_server_id == MEMBER:
            failed.append(event.consecutive_failures)

    context.runtime.event_bus.subscribe_to_all(observe, kind=HandlerKind.PROJECTION)

    def status(client: Any) -> dict[str, Any]:
        group = next(g for g in _tool(client, "hangar_group_list", {})["groups"] if g["group_id"] == GROUP)
        server = context.runtime.repository.get(MEMBER)
        return {"group": group, "server_state": str(server.state.value)}

    report: dict[str, Any] = {}
    with TestClient(mcp_app_for_serving(context.mcp_server), base_url="http://127.0.0.1:8000") as client:
        call = {"calls": [{"mcp_server": GROUP, "tool": TOOL, "arguments": {"x": "1"}}]}
        report["before"] = _tool(client, "hangar_call", call)
        report["ready"] = status(client)

        upstream.shutdown()
        upstream.server_close()
        report["refuses"] = _refuses(port)

        worker = _worker(context, "health_check")
        worker.start()
        group = GROUPS[GROUP]
        server = context.runtime.repository.get(MEMBER)
        deadline = time.monotonic() + DEADLINE_S
        while time.monotonic() < deadline and not (group.circuit_open and server.state.value == "degraded"):
            time.sleep(0.05)
        _stop_and_join(worker)

        report["after"] = status(client)

    report["health_checks_failed"] = list(failed)
    for each in context.runtime.repository.get_all().values():
        each.shutdown()
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
