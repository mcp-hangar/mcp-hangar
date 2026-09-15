"""Boot a `HangarConfig` through the facade and report what took effect (#1423).

Run as a script, in its own interpreter, by ``test_a_builder_config_takes_effect.py``:
``python _builder_served_harness.py <remote|discovery|filesystem> <out.json>``. Not
collected by pytest. The test runs it with ``HANGAR_CONFIG_STRICT=1``, so a key
the gateway does not read refuses the boot and the harness exits non-zero.

A separate process because ``bootstrap()`` fills process-global state -- the
runtime singleton, the discovery orchestrator, the source factory registry --
and two boots in one interpreter would read each other's.

* ``remote``: a remote server built with ``url=``, pointed at the in-process
  HTTP MCP upstream from ``_front_door_harness``. ``SyncHangar.from_builder``
  boots it; one ``hangar_call`` goes through the app ``serve --http`` serves
  (``mcp_app_for_serving``) and one through ``SyncHangar.invoke``.
* ``discovery``: ``enable_discovery(docker=True, kubernetes=True,
  filesystem=[dir])``. Reports the sources the orchestrator holds and the
  registry lists once the facade has started. Neither Docker nor a cluster has
  to be reachable: a source is built without connecting. Without the
  ``kubernetes`` extra the real factory raises ImportError and the gateway runs
  without that source by design, so a recording stand-in takes the factory's
  place and the entry the gateway read is still observed.
* ``filesystem``: start and stop, to show the facade runs discovery and stops it.

Naming: neutral placeholders only (store, read_item).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
from typing import Any

BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "builder-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
HEADERS = {
    "MCP-Protocol-Version": MODERN_VERSION,
    "Mcp-Method": "tools/call",
    "Mcp-Name": "hangar_call",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
SERVER = "store"
TOOL = "read_item"
DISCOVERY_THREAD = "mcp-hangar-discovery"


def _hangar_call(client: Any) -> dict[str, Any]:
    from _front_door_harness import jsonrpc

    params = {
        "name": "hangar_call",
        "arguments": {"calls": [{"mcp_server": SERVER, "tool": TOOL, "arguments": {"x": "1"}}]},
        "_meta": dict(ENVELOPE),
    }
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    response = client.post("/mcp", headers=HEADERS, content=body)
    response.raise_for_status()
    result = jsonrpc(response)["result"]
    return dict(json.loads(result["content"][0]["text"]))


def _remote(out: Path) -> dict[str, Any]:
    from http.server import ThreadingHTTPServer

    from _front_door_harness import Upstream
    from starlette.testclient import TestClient

    from mcp_hangar.facade import HangarConfig, SyncHangar
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    handler: type[Upstream] = type("_BuilderUpstream", (Upstream,), {"tools": (TOOL,), "called": []})
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    address = f"http://127.0.0.1:{upstream.server_address[1]}/mcp"

    hangar = SyncHangar.from_builder(HangarConfig().add_mcp_server(SERVER, mode="remote", url=address).build())
    hangar.start()
    context = hangar._hangar._context
    assert context is not None

    with TestClient(mcp_app_for_serving(context.mcp_server), base_url=BASE_URL) as client:
        batch = _hangar_call(client)
    invoked = hangar.invoke(SERVER, TOOL, {"x": "1"})
    spec = context.config["mcp_servers"][SERVER]
    hangar.stop()

    return {
        "address": address,
        "spec": spec,
        "batch": batch,
        "invoked": invoked,
        "upstream_called": list(handler.called),
    }


def _stand_in_kubernetes_if_absent() -> list[dict[str, Any]] | None:
    """Replace the kubernetes factory with a recorder when the extra is not installed.

    Returns the list the recorder fills with the config it is handed, or None
    when the real factory runs.
    """
    try:
        import kubernetes  # noqa: F401 -- probing for the optional extra
    except ImportError:
        pass
    else:
        return None

    from mcp_hangar.domain.discovery.discovery_source import DiscoverySource
    from mcp_hangar.infrastructure.discovery.registry import register_source_factory

    received: list[dict[str, Any]] = []

    class _Kubernetes(DiscoverySource):
        @property
        def source_type(self) -> str:
            return "kubernetes"

        async def discover(self) -> list[Any]:
            return []

        async def health_check(self) -> bool:
            return True

    def factory(mode: Any, config: dict[str, Any]) -> DiscoverySource:
        received.append(dict(config))
        return _Kubernetes(mode)

    register_source_factory("kubernetes", factory, replace=True)
    return received


def _discovery(out: Path) -> dict[str, Any]:
    from mcp_hangar.facade import HangarConfig, SyncHangar

    directory = out.parent / "servers.d"
    directory.mkdir()
    stand_in = _stand_in_kubernetes_if_absent()

    config = HangarConfig().enable_discovery(docker=True, kubernetes=True, filesystem=[str(directory)]).build()
    hangar = SyncHangar.from_builder(config)
    hangar.start()
    context = hangar._hangar._context
    assert context is not None
    orchestrator = context.discovery_orchestrator
    registry = context.discovery_registry

    report = {
        "directory": str(directory),
        "config": context.config["discovery"],
        "held": sorted(source.source_type for source in orchestrator.get_sources()),
        "registered": sorted(
            ([spec.source_type, spec.mode.value, spec.config] for spec in registry.get_all_sources()),
            key=lambda entry: entry[0],
        ),
        "running": orchestrator.get_stats()["running"],
        "kubernetes_stand_in": stand_in,
    }
    # With Docker unreachable the docker source may be waiting out a connection
    # backoff on discovery's loop; the stop ends that wait (#1436).
    hangar.stop()
    return report


def _discovery_thread_alive() -> bool:
    return any(thread.name == DISCOVERY_THREAD for thread in threading.enumerate())


def _filesystem(out: Path) -> dict[str, Any]:
    from mcp_hangar.facade import HangarConfig, SyncHangar

    directory = out.parent / "servers.d"
    directory.mkdir()

    hangar = SyncHangar.from_builder(HangarConfig().enable_discovery(filesystem=[str(directory)]).build())
    hangar.start()
    context = hangar._hangar._context
    assert context is not None
    orchestrator = context.discovery_orchestrator
    started = {"running": orchestrator.get_stats()["running"], "thread": _discovery_thread_alive()}
    hangar.stop()
    stopped = {"running": orchestrator.get_stats()["running"], "thread": _discovery_thread_alive()}
    return {"started": started, "stopped": stopped}


MODES = {"remote": _remote, "discovery": _discovery, "filesystem": _filesystem}


def main(mode: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data
    report = MODES[mode](out)
    out.write_text(json.dumps(report, default=str))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
