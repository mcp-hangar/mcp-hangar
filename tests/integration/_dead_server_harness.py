"""Bootstrap Hangar, break servers until the recovery saga gives up, then revive them (#1361, #1359).

Run as a script, in its own interpreter, by
``test_a_given_up_server_reads_dead_on_the_served_path.py``:
``python _dead_server_harness.py <mode> <out.json>``. Not collected by pytest.
The same file is the upstream every server runs: ``python _dead_server_harness.py
upstream <flag>`` serves MCP over stdio and writes its pid to ``<flag>.pid``.
While ``<flag>`` exists, a running one fails ``tools/list``, the call a health
check makes, and a new one exits before it answers anything, so a start fails.

Not a new one that stays up and fails ``tools/list``: a start like that is a
separate defect, tracked separately, and would stall this harness.

A separate process because ``bootstrap()`` fills process-global state -- the
runtime, the saga manager, the metrics registry -- that a second bootstrap in
the same interpreter would inherit.

What runs is production: ``bootstrap()`` with a config dict; the app
``serve --http`` serves, under starlette's ``TestClient``, for ``hangar_call``,
``hangar_start``, ``hangar_stop`` and ``hangar_group_list``; the health and GC
workers ``bootstrap()`` created, started the way ``ServerLifecycle.start`` starts
them; and the sagas it registered. The recovery saga's restarts go through the
command bus to a real start of the upstream process. Metrics are read from
``get_metrics()``, the body ``/metrics`` returns. Nothing here publishes an
event or sends a command by hand.

Three things are changed, none on the path under test. The workers' intervals,
60s and 30s in production, are lowered before ``bootstrap()`` reads them. And
the recovery saga gets one restart instead of three, after 2.5s instead of 5s.
Not sooner: a restart the saga schedules before the server's own backoff has
run out is refused, and nothing schedules another. With
``max_consecutive_failures: 1`` that backoff is 2s after the first failure.

Modes:

- ``single``: two servers. Both are broken until the saga gives up. One is
  revived by ``hangar_start``; the other is called inside its backoff, which is
  refused, and again after it, which revives it.
- ``group``: a one-member group. The member's process is killed between two
  requests: a crash, which a call through the group restarts. Then its upstream
  is broken until the saga gives up: the member leaves rotation and a call
  through the group is refused, until ``hangar_start`` brings it back.
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
    "io.modelcontextprotocol/clientInfo": {"name": "dead-server-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

#: Single mode: one is revived by an explicit start, the other by a call.
BY_START, BY_CALL = "svc-a", "svc-b"
#: Group mode.
GROUP, MEMBER = "pool", "member-a"
#: The saga's first backoff and retry budget; see the module docstring.
SAGA_BACKOFF_S = 2.5
SAGA_MAX_RETRIES = 1
#: A give-up takes one failed check, 2.5s, and one failed start.
GIVE_UP_DEADLINE_S = 20.0
#: How long the workers run with a server dead, or cold.
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


def _call(client: Any, target: str) -> dict[str, Any]:
    """``hangar_call`` add on a server or a group; the batch result."""
    call = {"mcp_server": target, "tool": "add", "arguments": {"a": 1, "b": 2}}
    return _tool(client, "hangar_call", {"calls": [call]})


def _group(client: Any) -> dict[str, Any]:
    """The group as ``hangar_group_list`` reports it -- what the operator sees."""
    return next(g for g in _tool(client, "hangar_group_list", {})["groups"] if g["group_id"] == GROUP)


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


def _saw_dead(seen: list[list[Any]], server: str, reason: str) -> bool:
    """The watcher heard the server go dead for ``reason``.

    Waited on rather than the aggregate's state, which flips before the event
    is delivered: the watcher is subscribed after the metrics handler and the
    sagas, so once it has the event, so have they.
    """
    return [server, "state", "dead", reason] in seen


def _give_up_delivered(seen: list[list[Any]], server: str) -> bool:
    """The publish that carried the give-up has finished.

    The give-up is delivered inside the last degrade's publish -- the saga
    answers that event with a nested one -- so the watcher hears `dead` and
    then that degrade. Only then is everything it drove on the gauges.
    """
    events = [e[1:] for e in seen if e[0] == server]
    marker = ["state", "dead", "given_up"]
    return marker in events and any(e[0] == "degraded" for e in events[events.index(marker) :])


def _worker(context: Any, task: str) -> Any:
    return next(w for w in context.background_workers if getattr(w, "task", None) == task)


def _config(mode: str, flags: dict[str, Path]) -> dict[str, Any]:
    servers: dict[str, Any] = {
        server: {
            "mode": "subprocess",
            "command": [sys.executable, str(HERE), "upstream", str(flag)],
            "max_consecutive_failures": 1,
        }
        for server, flag in flags.items()
    }
    if mode == "group":
        servers[GROUP] = {
            "mode": "group",
            "strategy": "round_robin",
            "min_healthy": 1,
            # Failures never take the member out: only the give-up may, so the
            # test can see that it did.
            "health": {"unhealthy_threshold": 100, "healthy_threshold": 1},
            "circuit_breaker": {"failure_threshold": 100, "reset_timeout_s": 3600},
            "members": [{"id": MEMBER}],
        }
    return {"mcp_servers": servers}


def _backoff_over(health: Any) -> bool:
    """Past the server's backoff at its jitter ceiling, +10%.

    Not `health.can_retry()`: it draws fresh jitter every time, so it can say
    yes here and the executor's own draw say no a moment later.
    """
    ceiling = min(60.0, 2.0**health.consecutive_failures) * 1.1
    return time.time() - (health.last_failure_at or 0.0) >= ceiling


def _single(
    client: Any, repository: Any, flags: dict[str, Path], seen: list[list[Any]], report: dict[str, Any]
) -> None:
    def snapshot() -> dict[str, Any]:
        return {server: _snapshot(repository, server) for server in flags}

    def events_since(mark: int) -> dict[str, list[list[Any]]]:
        return {server: [e[1:] for e in seen[mark:] if e[0] == server] for server in flags}

    # Both serve, and a health check sees both working.
    report["first_calls"] = {server: _call(client, server) for server in flags}
    _wait(lambda: all([server, "HealthCheckPassed", None] in seen for server in flags), 10)
    report["healthy"] = snapshot()

    # Both upstreams go bad. The next check fails and degrades them; from here
    # no check can pass, so last-healthy is what it will stay.
    for flag in flags.values():
        flag.touch()
    _wait(lambda: all(repository.get(server).state.value != "ready" for server in flags), 10)
    report["broken"] = snapshot()

    # The saga restarts each, the restart fails, and it gives up.
    _wait(lambda: all(_give_up_delivered(seen, server) for server in flags), GIVE_UP_DEADLINE_S)
    report["dead"] = snapshot()
    report["dead_mark"] = len(seen)

    # A call inside the server's backoff: refused, and nothing is started.
    report["call_in_backoff"] = _call(client, BY_CALL)

    # Dead, with the health and GC workers running.
    mark = len(seen)
    time.sleep(QUIET_WINDOW_S)
    report["while_dead"] = {"snapshot": snapshot(), "events": events_since(mark)}

    # The upstreams are back. One server is started, the other called once its
    # backoff has run out.
    for flag in flags.values():
        flag.unlink()
    report["revived"] = {BY_START: _tool(client, "hangar_start", {"mcp_server": BY_START})}
    _wait(lambda: _backoff_over(repository.get(BY_CALL).health), GIVE_UP_DEADLINE_S)
    report["revived"][BY_CALL] = _call(client, BY_CALL)
    report["after_revival"] = snapshot()

    # Stopped: cold, and not probed while it is.
    report["stop"] = _tool(client, "hangar_stop", {"mcp_server": BY_START})
    report["stopped"] = _snapshot(repository, BY_START)
    time.sleep(QUIET_WINDOW_S)
    report["still_cold"] = _snapshot(repository, BY_START)


def _grouped(
    client: Any, repository: Any, flags: dict[str, Path], seen: list[list[Any]], report: dict[str, Any]
) -> None:
    flag = flags[MEMBER]
    member = repository.get(MEMBER)

    report["first_call"] = _call(client, GROUP)
    report["healthy"] = _group(client)

    # The process dies between two requests: a crash.
    os.kill(int(Path(f"{flag}.pid").read_text()), signal.SIGKILL)
    _wait(lambda: _saw_dead(seen, MEMBER, "crashed"), 10)
    report["crashed"] = _group(client)
    report["crash_call"] = _call(client, GROUP)
    report["after_crash_call"] = _group(client)

    # Its upstream goes bad until the saga gives up.
    flag.touch()
    _wait(lambda: member.state.value != "ready", 10)
    _wait(lambda: _give_up_delivered(seen, MEMBER), GIVE_UP_DEADLINE_S)
    report["given_up"] = _group(client)
    report["given_up_call"] = _call(client, GROUP)

    # Back, and started on purpose.
    flag.unlink()
    report["start"] = _tool(client, "hangar_start", {"mcp_server": MEMBER})
    report["after_start"] = _group(client)
    report["start_call"] = _call(client, GROUP)


def main(mode: str, out: Path) -> None:
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

    names = [MEMBER] if mode == "group" else [BY_START, BY_CALL]
    flags = {server: out.parent / f"{server}.down" for server in names}
    context = bootstrap(config_dict=_config(mode, flags))
    recovery = get_saga_manager()._event_sagas["mcp_server_recovery"]
    recovery._initial_backoff_s = SAGA_BACKOFF_S
    recovery._max_retries = SAGA_MAX_RETRIES

    seen: list[list[Any]] = []

    def observe(event: DomainEvent) -> None:
        server = getattr(event, "mcp_server_id", None)
        if server not in flags:
            return
        if isinstance(event, McpServerStateChanged):
            seen.append([server, "state", event.new_state, event.dead_reason])
        elif isinstance(event, McpServerDegraded):
            seen.append([server, "degraded", event.reason])
        elif isinstance(event, HealthCheckPassed | HealthCheckFailed):
            seen.append([server, type(event).__name__, None])

    # Watches only. Subscribed after the saga manager, so it hears an event
    # after the saga has.
    context.runtime.event_bus.subscribe_to_all(observe, kind=HandlerKind.PROJECTION)

    repository = context.runtime.repository
    health, gc = _worker(context, "health_check"), _worker(context, "gc")
    report: dict[str, Any] = {}
    with TestClient(mcp_app_for_serving(context.mcp_server), base_url=BASE_URL) as client:
        health.start()
        gc.start()
        if mode == "group":
            _grouped(client, repository, flags, seen, report)
        else:
            _single(client, repository, flags, seen, report)

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
        main(sys.argv[1], Path(sys.argv[2]))
