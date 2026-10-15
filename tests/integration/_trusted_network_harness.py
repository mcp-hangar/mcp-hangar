"""Register a remote server over ``POST /api/mcp_servers`` at a private address, with and without trust (#1752).

Run as a script, in its own interpreter, by
``test_a_trusted_network_admits_an_api_registered_endpoint.py``:
``python _trusted_network_harness.py <out.json>``. Not collected by pytest.

What runs is production: ``bootstrap()`` with a config dict, the app
``serve --http`` serves (``/api`` beside ``/mcp``) under starlette's ``TestClient``, the REST route the
Kubernetes operator calls, and ``hangar_call`` to a real HTTP upstream. The
upstream listens on this host's own private address -- not loopback, which
``ssrf.trusted_networks`` can never cover -- so the strict policy refuses it
exactly as it refuses an in-cluster Service. ``apply_process_config`` is the
function startup and a reload both apply the process-wide sections with.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

TOOL = "add"


def private_address() -> str | None:
    """This host's address on its default route, if the strict policy refuses it.

    A UDP connect sends nothing; it only picks the source address.
    """
    from mcp_hangar.domain.security.ssrf import endpoint_is_a_literal_the_strict_policy_refuses

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))
        except OSError:
            return None
        address = str(probe.getsockname()[0])
    if address.startswith("127.") or not endpoint_is_a_literal_the_strict_policy_refuses(f"http://{address}/"):
        return None
    return address


def _config(trusted: list[str] | None) -> dict[str, Any]:
    config: dict[str, Any] = {"mcp_servers": {}, "config_reload": {"enabled": False}}
    if trusted is not None:
        config["ssrf"] = {"trusted_networks": trusted}
    return config


def main(out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    address = private_address()
    if address is None:
        out.write_text(json.dumps({"skipped": "no private, non-loopback address on the default route"}))
        os._exit(0)

    from _front_door_harness import Upstream
    from _group_recovery_harness import _tool
    from _reload_served_harness import BASE_URL, _served_app
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.config import apply_process_config
    from mcp_hangar.server.lifecycle import ServerLifecycle

    handler: Any = type("_TrustedUpstream", (Upstream,), {"tools": (TOOL,), "called": [], "holds": {}})
    upstream = ThreadingHTTPServer((address, 0), handler)
    port = upstream.server_address[1]
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    endpoint = f"http://{address}:{port}/mcp"

    def register(client: Any, mcp_server_id: str) -> dict[str, Any]:
        body = {"mcp_server_id": mcp_server_id, "mode": "remote", "endpoint": endpoint}
        response = client.post("/api/mcp_servers", json=body)
        return {"status": response.status_code, "body": response.json()}

    def call(client: Any, mcp_server_id: str) -> dict[str, Any]:
        result = _tool(client, "hangar_call", {"calls": [{"mcp_server": mcp_server_id, "tool": TOOL, "arguments": {}}]})
        [outcome] = result["results"]
        return {"success": outcome.get("success"), "error": outcome.get("error")}

    report: dict[str, Any] = {"address": address}
    context = bootstrap(config_dict=_config(None))
    with TestClient(_served_app(context, ServerLifecycle(context)), base_url=BASE_URL) as client:
        report["untrusted"] = register(client, "before-trust")

        apply_process_config(_config([f"{address}/32"]))
        report["trusted"] = register(client, "in-cluster")
        report["trusted_call"] = call(client, "in-cluster")
        report["upstream_called"] = list(handler.called)

        # A reload that drops the section: the next connection is refused, and
        # so is the next registration.
        apply_process_config(_config(None))
        report["revoked_call"] = call(client, "in-cluster")
        report["revoked_register"] = register(client, "after-revoke")
        report["upstream_called_after_revoke"] = list(handler.called)

    for each in context.runtime.repository.get_all().values():
        each.shutdown()
    upstream.shutdown()
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
