"""Boot Hangar on SQLite, push an L7 egress policy over REST, then boot it again (#1306).

Run as a script, in its own interpreter, by
``test_a_restart_keeps_an_l7_policy_set_over_the_api.py``:
``python _restart_l7_policy_harness.py <workdir> <phase> <out.json>``, once per
phase -- ``before``, ``after``, ``clear``, ``after_clear`` -- over the same data
directory, and with ``memory`` then ``memory_after``, which have no persistence
backend at all. Every phase also scrapes ``/metrics`` (#1562). Not
collected by pytest. Each phase is its own process because a restart is one:
``bootstrap()`` fills process-global state, and nothing may survive from the
first gateway to the second except what it stored.

``math`` is declared in the file. The operator's policy has no file key, so the
push writes it only to the server's fleet row -- and startup recovery skipped
the row of every server the file declares, so the second gateway served ``add``
ungoverned while the CR still reported the policy.

What runs is production: ``bootstrap(config_path=...)`` on the SQLite
persistence backend, the app ``serve --http`` serves, the policy through ``POST
/api/mcp_servers/{id}/l7_policy``, and the calls through ``hangar_call`` over
streamable HTTP, against a real stdio subprocess.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from _reload_keeps_harness import _server
from _reload_l7_policy_harness import L7_POLICY, _L7Gateway
from _reload_served_harness import BASE_URL, _served_app, _write

SERVER = "math"


def _config(data_dir: Path, phase: str) -> dict[str, Any]:
    config: dict[str, Any] = {"config_reload": {"enabled": False}, "mcp_servers": {SERVER: _server()}}
    # ``memory`` is the chart default: no persistence backend at all.
    if not phase.startswith("memory"):
        config["persistence"] = {"backend": "sqlite", "sqlite": {"data_dir": str(data_dir)}}
    return config


def _scrape() -> dict[str, Any]:
    """The L7 policy series of ``GET /metrics``, through the endpoint ``serve --http`` mounts."""
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from mcp_hangar.server.lifecycle import metrics_endpoint

    with TestClient(Starlette(routes=[Route("/metrics", metrics_endpoint, methods=["GET"])])) as client:
        text = client.get("/metrics").text
    held = re.findall(r'^mcp_hangar_l7_policy_held\{mcp_server="([^"]+)",mode="([^"]+)"\} (\S+)$', text, re.M)
    last_set = re.findall(
        r'^mcp_hangar_l7_policy_last_set_timestamp_seconds\{mcp_server="([^"]+)"\} (\S+)$', text, re.M
    )
    return {
        "held": {server: [mode, float(value)] for server, mode, value in held},
        "last_set": {server: float(value) for server, value in last_set},
        "family": "# TYPE mcp_hangar_l7_policy_held gauge" in text,
    }


def run(workdir: Path, phase: str) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import ServerLifecycle

    path = workdir / "config.yaml"
    _write(path, _config(workdir / "data", phase))
    context = bootstrap(config_path=str(path))
    out: dict[str, Any] = {}

    with TestClient(_served_app(context, ServerLifecycle(context)), base_url=BASE_URL) as client:
        gateway = _L7Gateway(client, {})
        if phase in ("before", "memory"):
            out["unset"] = {"policy": gateway.l7(SERVER), "call": gateway.call(SERVER)}
            pushed = client.post(f"/api/mcp_servers/{SERVER}/l7_policy", json=L7_POLICY)
            out["set"] = {"status": pushed.status_code, "body": pushed.json()}
        if phase == "clear":
            out["cleared"] = client.delete(f"/api/mcp_servers/{SERVER}/l7_policy").status_code
        out["policy"] = gateway.l7(SERVER)
        out["call"] = gateway.call(SERVER)

    out["metrics"] = _scrape()

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    return out


def main(workdir: Path, phase: str, out: Path) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    out.write_text(json.dumps(run(workdir, phase)))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
