"""Bootstrap Hangar, break two servers until the recovery saga gives up, then revive them (#1361, #1359).

Run as a script, in its own interpreter, by
``test_a_given_up_server_reads_dead_on_the_served_path.py``:
``python _dead_server_harness.py <out.json>``. Not collected by pytest. The same
file is the upstream both servers run: ``python _dead_server_harness.py
upstream <flag>`` serves MCP over stdio. While ``<flag>`` exists, a running one
fails ``tools/list``, the call a health check makes, and a new one exits before
it answers anything, so a start fails.

Not a new one that stays up and fails ``tools/list``: such a start never ends.
It blocks reading the live process's stderr for diagnostics, and the server
sits in ``initializing`` with nothing to give up on. That is its own defect,
reported with this change rather than worked around in it.

A separate process because ``bootstrap()`` fills process-global state -- the
runtime, the saga manager, the metrics registry -- that a second bootstrap in
the same interpreter would inherit.

What runs is production: ``bootstrap()`` with a config dict; the app
``serve --http`` serves, under starlette's ``TestClient``, for ``hangar_call``,
``hangar_start`` and ``hangar_stop``; the health and GC workers ``bootstrap()``
created, started the way ``ServerLifecycle.start`` starts them; and the
recovery saga it registered, whose restarts go through the command bus to a
real start of the upstream process. Metrics are read from ``get_metrics()``,
the body ``/metrics`` returns. Nothing here publishes an event or sends a
command by hand.

Two things are changed, neither on the path under test. The workers'
intervals, 60s and 30s in production, are lowered before ``bootstrap()`` reads
them. And the saga's first backoff is 2.5s instead of 5s, so its three retries
take about 17s. Not less: a restart the saga schedules before the server's own
backoff has run out is refused, and nothing schedules another. With
``max_consecutive_failures: 1`` that backoff is 2s, 4s and 8s after the first,
second and third failure, against the saga's 2.5s, 5s and 10s.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

HERE = Path(__file__).resolve()
BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "dead-server-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

#: One is revived by an explicit start, the other by a call.
BY_START, BY_CALL = "svc-a", "svc-b"
SERVERS = (BY_START, BY_CALL)
#: The saga's first backoff; see the module docstring.
SAGA_BACKOFF_S = 2.5
#: Three retries take 2.5 + 5 + 10s, plus the starts themselves.
GIVE_UP_DEADLINE_S = 35.0
#: How long the workers run with both servers dead, and with one cold.
QUIET_WINDOW_S = 3.0

ADD = {
    "name": "add",
    "description": "Add two numbers",
    "inputSchema": {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    },
}


def upstream(flag: Path) -> None:
    """An MCP server over stdio that fails while ``flag`` exists; see the module docstring."""
    if flag.exists():
        sys.exit(1)
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
        elif method == "tools/list" and flag.exists():
            reply["error"] = {"code": -32603, "message": "upstream is down"}
        elif method == "tools/list":
            reply["result"] = {"tools": [ADD]}
        elif method == "tools/call":
            arguments = request["params"]["arguments"]
            reply["result"] = {"content": [{"type": "text", "text": json.dumps(arguments["a"] + arguments["b"])}]}
        else:
            reply["error"] = {"code": -32601, "message": f"unknown method: {method}"}
        print(json.dumps(reply), flush=True)


def _tool(client: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One stateless ``tools/call`` POST to ``/mcp``; the tool's JSON result."""
    headers = {
        "MCP-Protocol-Version": MODERN_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": name,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    params = {"name": name, "arguments": arguments, "_meta": ENVELOPE}
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    response = client.post("/mcp", headers=headers, content=body)
    response.raise_for_status()
    text = response.text.lstrip()
    if not text.startswith("{"):  # SSE framing: take the data line
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    result = json.loads(text)["result"]
    return json.loads(result["content"][0]["text"])


def _call(client: Any, server: str) -> dict[str, Any]:
    """``hangar_call`` add on one server; the batch result."""
    call = {"mcp_server": server, "tool": "add", "arguments": {"a": 1, "b": 2}}
    return _tool(client, "hangar_call", {"calls": [call]})


def _samples(name: str, server: str, **labels: str) -> list[float]:
    """Every sample of ``name`` for ``server`` (and ``labels``) in the /metrics body."""
    from mcp_hangar.metrics import get_metrics

    wanted = [f'mcp_server="{server}"', *(f'{key}="{value}"' for key, value in labels.items())]
    return [
        float(line.split()[-1])
        for line in get_metrics().splitlines()
        if line.startswith(name + "{") and all(label in line.split("}")[0] for label in wanted)
    ]


def _one(name: str, server: str) -> float | None:
    values = _samples(name, server)
    return values[0] if values else None


def _snapshot(repository: Any, server: str) -> dict[str, Any]:
    return {
        "domain_state": repository.get(server).state.value,
        "state": _one("mcp_hangar_mcp_server_state", server),
        "up": _one("mcp_hangar_mcp_server_up", server),
        "last_healthy": _one("mcp_hangar_mcp_server_last_healthy_timestamp_seconds", server),
        "health_checks": sum(_samples("mcp_hangar_health_checks_total", server)),
        "stops": {
            reason: sum(_samples("mcp_hangar_mcp_server_stops_total", server, reason=reason))
            for reason in ("max_retries_exceeded", "shutdown")
        },
    }


def _wait(condition: Callable[[], bool], deadline_s: float) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def _worker(context: Any, task: str) -> Any:
    return next(w for w in context.background_workers if getattr(w, "task", None) == task)


def main(out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from starlette.testclient import TestClient

    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import (
        DomainEvent,
        HealthCheckFailed,
        HealthCheckPassed,
        McpServerDegraded,
        McpServerStateChanged,
    )
    from mcp_hangar.infrastructure.saga_manager import get_saga_manager
    from mcp_hangar.server.bootstrap import bootstrap, workers
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    workers.HEALTH_CHECK_INTERVAL_SECONDS = 1
    workers.GC_WORKER_INTERVAL_SECONDS = 1

    flags = {server: out.parent / f"{server}.down" for server in SERVERS}
    config = {
        "mcp_servers": {
            server: {
                "mode": "subprocess",
                "command": [sys.executable, str(HERE), "upstream", str(flags[server])],
                "max_consecutive_failures": 1,
            }
            for server in SERVERS
        }
    }
    context = bootstrap(config_dict=config)
    get_saga_manager()._event_sagas["mcp_server_recovery"]._initial_backoff_s = SAGA_BACKOFF_S

    seen: list[list[Any]] = []

    def observe(event: DomainEvent) -> None:
        server = getattr(event, "mcp_server_id", None)
        if server not in SERVERS:
            return
        if isinstance(event, McpServerStateChanged):
            seen.append([server, "state", event.new_state])
        elif isinstance(event, McpServerDegraded):
            seen.append([server, "degraded", event.reason])
        elif isinstance(event, HealthCheckPassed | HealthCheckFailed):
            seen.append([server, type(event).__name__, None])

    # Watches only. Subscribed after the saga manager, so it hears an event
    # after the saga has.
    context.runtime.event_bus.subscribe_to_all(observe, kind=HandlerKind.PROJECTION)

    repository = context.runtime.repository
    health, gc = _worker(context, "health_check"), _worker(context, "gc")

    def snapshot() -> dict[str, Any]:
        return {server: _snapshot(repository, server) for server in SERVERS}

    def events_since(mark: int) -> dict[str, list[list[Any]]]:
        return {server: [e[1:] for e in seen[mark:] if e[0] == server] for server in SERVERS}

    report: dict[str, Any] = {}
    with TestClient(mcp_app_for_serving(context.mcp_server), base_url=BASE_URL) as client:
        # Both serve, and a health check sees both working.
        report["first_calls"] = {server: _call(client, server) for server in SERVERS}
        health.start()
        gc.start()
        _wait(lambda: all([server, "HealthCheckPassed", None] in seen for server in SERVERS), 10)
        report["healthy"] = snapshot()

        # Both upstreams go bad. The next check fails and degrades them; from
        # here no check can pass, so last-healthy is what it will stay.
        for flag in flags.values():
            flag.touch()
        _wait(lambda: all(repository.get(server).state.value != "ready" for server in SERVERS), 10)
        report["broken"] = snapshot()

        # The saga retries, each restart fails, and it gives up.
        _wait(lambda: all(repository.get(server).state.value == "dead" for server in SERVERS), GIVE_UP_DEADLINE_S)
        report["dead"] = snapshot()

        # Dead, with the health and GC workers running.
        mark = len(seen)
        report["dead_mark"] = mark
        time.sleep(QUIET_WINDOW_S)
        report["while_dead"] = {"snapshot": snapshot(), "events": events_since(mark)}

        # The upstreams are back. One server is started, the other called.
        for flag in flags.values():
            flag.unlink()
        report["revived"] = {
            BY_START: _tool(client, "hangar_start", {"mcp_server": BY_START}),
            BY_CALL: _call(client, BY_CALL),
        }
        report["after_revival"] = snapshot()

        # Stopped: cold, and not probed while it is.
        report["stop"] = _tool(client, "hangar_stop", {"mcp_server": BY_START})
        report["stopped"] = _snapshot(repository, BY_START)
        time.sleep(QUIET_WINDOW_S)
        report["still_cold"] = _snapshot(repository, BY_START)

    health.stop()
    gc.stop()
    report["events"] = seen
    for server in repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    if sys.argv[1] == "upstream":
        upstream(Path(sys.argv[2]))
    else:
        main(Path(sys.argv[1]))
