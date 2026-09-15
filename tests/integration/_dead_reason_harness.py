"""Bootstrap Hangar, drive one server dead for each reason, and read every surface (#1418).

Run as a script, in its own interpreter, by
``test_a_dead_servers_reason_reads_the_same_on_every_surface.py``:
``python _dead_reason_harness.py <out.json>``. Not collected by pytest. The same
file is the upstream every server runs: ``python _dead_reason_harness.py
upstream <flag> <tools>`` serves MCP over stdio, with the comma-separated
``tools``, and writes its pid to ``<flag>.pid``. While ``<flag>`` exists, a
running one fails ``tools/list``, and a new one exits before it answers
anything, so a start fails. Either way it says ``SENTINEL``, which no read
surface may repeat.

A separate process because ``bootstrap()`` fills process-global state that a
second bootstrap in the same interpreter would inherit.

What runs is production: ``bootstrap()`` with a config dict, and the served app
as ``ServerLifecycle.run_http`` assembles it with auth off: the MCP app
``mcp_app_for_serving`` returns, and the REST router under ``/api``. The health
worker ``bootstrap()`` created finds the crash and the broken upstream, and the
recovery saga it registered gives up. Only the worker's interval and the saga's
budget are changed, as ``_dead_server_harness.py`` changes them.

The servers:

- ``gave-up``: its upstream breaks. A health check degrades it, the saga's one
  restart fails, and the saga gives up: ``given_up``.
- ``crashed``: its process is killed, and a health check finds it: ``crashed``.
- ``start-failed``: its upstream exits before it answers, and ``hangar_start``
  fails: ``start_failed``.
- ``blocked``: it serves a tool outside its ``expected_tools``, in block mode,
  and ``hangar_start`` fails: ``capability_blocked``.
- ``healthy``: ready throughout, so it is not dead on any surface.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any

HERE = Path(__file__).resolve()
BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "dead-reason-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

#: Text only the upstream sends.
SENTINEL = "SENTINEL-1418-what-the-upstream-said"

GAVE_UP, CRASHED, START_FAILED, BLOCKED, HEALTHY = "gave-up", "crashed", "start-failed", "blocked", "healthy"
#: Each server driven dead, and the reason it must read.
REASONS = {
    GAVE_UP: "given_up",
    CRASHED: "crashed",
    START_FAILED: "start_failed",
    BLOCKED: "capability_blocked",
}
SERVERS = (*REASONS, HEALTHY)
#: The saga's first backoff and retry budget, as `_dead_server_harness.py` sets them.
SAGA_BACKOFF_S = 2.5
SAGA_MAX_RETRIES = 1
#: A give-up takes one failed check and one failed restart, 2.5s after it.
DEAD_DEADLINE_S = 30.0


def upstream(flag: Path, tools: list[str]) -> None:
    """An MCP server over stdio that fails while ``flag`` exists; see the module docstring."""
    if flag.exists():
        print(SENTINEL, file=sys.stderr, flush=True)
        sys.exit(1)
    Path(f"{flag}.pid").write_text(str(os.getpid()))
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue  # a notification
        method = request.get("method")
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": request["id"]}
        if method == "initialize":
            reply["result"] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "breakable", "version": "0"},
            }
        elif method == "tools/list" and not flag.exists():
            reply["result"] = {"tools": [{"name": name, "inputSchema": {"type": "object"}} for name in tools]}
        else:
            reply["error"] = {"code": -32603, "message": SENTINEL}
        print(json.dumps(reply), flush=True)


def _served_app(context: Any) -> Any:
    """The app ``ServerLifecycle.run_http`` serves with auth off: ``/api`` to the REST router, the rest to MCP."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from mcp_hangar.server.api import create_api_router
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    mcp_app = mcp_app_for_serving(context.mcp_server)
    aux_app = Starlette(routes=[Mount("/api", app=create_api_router(auth_components=context.auth_components))])

    async def combined_app(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == "/api" or path.startswith("/api/"):
                await aux_app(scope, receive, send)
                return
        await mcp_app(scope, receive, send)

    return combined_app


class _Reader:
    """Reads the surfaces over the served app, and keeps every body it was sent."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.bodies: list[str] = []

    def rest(self, route: str) -> Any:
        response = self._client.get(f"/api{route}")
        self.bodies.append(response.text)
        response.raise_for_status()
        return response.json()

    def tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """One stateless ``tools/call`` POST to ``/mcp``: the tool's JSON result, or its error text."""
        headers = {
            "MCP-Protocol-Version": MODERN_VERSION,
            "Mcp-Method": "tools/call",
            "Mcp-Name": name,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        params = {"name": name, "arguments": arguments, "_meta": ENVELOPE}
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
        response = self._client.post("/mcp", headers=headers, content=body)
        response.raise_for_status()
        text = response.text.lstrip()
        if not text.startswith("{"):  # SSE framing: take the data line
            text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
        result = json.loads(text)["result"]
        content = result["content"][0]["text"]
        if result.get("isError"):
            return {"error": content}
        self.bodies.append(text)
        return json.loads(content)


def _wait(condition: Callable[[], bool], deadline_s: float) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def _config(flags: dict[str, Path]) -> dict[str, Any]:
    def server(name: str, tools: str = "add", **options: Any) -> dict[str, Any]:
        command = [sys.executable, str(HERE), "upstream", str(flags[name]), tools]
        return {"mode": "subprocess", "command": command, **options}

    blocking = {"tools": {"expected_tools": ["add"]}, "enforcement_mode": "block"}
    return {
        "mcp_servers": {
            GAVE_UP: server(GAVE_UP, max_consecutive_failures=1),
            CRASHED: server(CRASHED),
            START_FAILED: server(START_FAILED),
            BLOCKED: server(BLOCKED, "add,exfiltrate", capabilities=blocking),
            HEALTHY: server(HEALTHY),
        }
    }


def _surfaces(reader: _Reader, server: str, listed: list[Any], tool_list: Any, status: Any) -> dict[str, Any]:
    return {
        "GET /api/mcp_servers/{id}": reader.rest(f"/mcp_servers/{server}"),
        "GET /api/mcp_servers": next(e for e in listed if e["mcp_server_id"] == server),
        "hangar_details": reader.tool("hangar_details", {"mcp_server": server}),
        "hangar_list": next(e for e in tool_list["mcp_servers"] if e["mcp_server_id"] == server),
        "hangar_status": next(e for e in status["mcp_servers"] if e["id"] == server),
    }


def _drive(reader: _Reader, repository: Any, flags: dict[str, Path], report: dict[str, Any]) -> None:
    report["starts"] = {server: reader.tool("hangar_start", {"mcp_server": server}) for server in (GAVE_UP, CRASHED)}
    report["starts"][HEALTHY] = reader.tool("hangar_start", {"mcp_server": HEALTHY})
    report["starts"][BLOCKED] = reader.tool("hangar_start", {"mcp_server": BLOCKED})
    flags[START_FAILED].touch()
    report["starts"][START_FAILED] = reader.tool("hangar_start", {"mcp_server": START_FAILED})
    os.kill(int(Path(f"{flags[CRASHED]}.pid").read_text()), signal.SIGKILL)
    flags[GAVE_UP].touch()

    def all_dead() -> bool:
        return all(repository.get(server).dead_reason_snapshot == reason for server, reason in REASONS.items())

    report["all_dead"] = _wait(all_dead, DEAD_DEADLINE_S)
    report["domain"] = {
        server: [repository.get(server).state.value, repository.get(server).dead_reason_snapshot] for server in SERVERS
    }

    # What an operator reads. Every list is read once, then each server's entry taken from it.
    listed = reader.rest("/mcp_servers")["mcp_servers"]
    tool_list = reader.tool("hangar_list", {})
    status = reader.tool("hangar_status", {})
    report["surfaces"] = {server: _surfaces(reader, server, listed, tool_list, status) for server in SERVERS}
    report["bodies"] = list(reader.bodies)
    # Where the upstream's own output goes, and is meant to: the sentinel
    # reached Hangar. Read after the bodies are taken, so it is not one of them.
    report["upstream_logs"] = reader.rest(f"/mcp_servers/{START_FAILED}/logs")


def main(out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from starlette.testclient import TestClient

    from mcp_hangar.infrastructure.saga_manager import get_saga_manager
    from mcp_hangar.server.bootstrap import bootstrap, workers

    workers.HEALTH_CHECK_INTERVAL_SECONDS = 1
    workers.GC_WORKER_INTERVAL_SECONDS = 1

    flags = {server: out.parent / f"{server}.down" for server in SERVERS}
    context = bootstrap(config_dict=_config(flags))
    recovery = get_saga_manager()._event_sagas["mcp_server_recovery"]
    recovery._initial_backoff_s = SAGA_BACKOFF_S
    recovery._max_retries = SAGA_MAX_RETRIES

    repository = context.runtime.repository
    health = next(w for w in context.background_workers if getattr(w, "task", None) == "health_check")
    report: dict[str, Any] = {}
    with TestClient(_served_app(context), base_url=BASE_URL) as client:
        health.start()
        _drive(_Reader(client), repository, flags, report)

    health.stop()
    for server in repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker thread is a daemon mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    if sys.argv[1] == "upstream":
        upstream(Path(sys.argv[2]), sys.argv[3].split(","))
    else:
        main(Path(sys.argv[1]))
