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

What a command drove is read only once its events have reached a watcher
subscribed after the metrics handler and the sagas. The health and GC workers
publish whatever a server has pending, so a command can return before its own
events are delivered, and reading at once would read the state before it.

Three things are changed, none on the path under test. The workers' intervals,
60s and 30s in production, are lowered before ``bootstrap()`` reads them. And
outside ``defaults`` mode the recovery saga gets one restart instead of three,
after 2.5s instead of 5s, which is past the server's own backoff: 2s after the
first failure with ``max_consecutive_failures: 1``. So in those modes the
restart runs at once. A restart the saga schedules inside that backoff is
refused and scheduled again (#1401); ``defaults`` mode shows that.

In ``failover`` mode B's backoff is also widened, to 60s, for the twenty calls
(#1411). At its real 2s, jitter included, it runs out between B's two turns
when calls are about a second apart. Call 3 then starts B again, a second
``McpServerStartError``, where a faster run sees ``CircuitBreakerOpen``. The
refusal is still the server's own: the call gate and the start both read the
same tracker. Only how long it lasts is fixed, so the expected sequence does not
depend on how fast the calls go.

Two env knobs slow ``failover`` mode down, to show that its result does not
depend on pace. Both are off by default:

- ``DEAD_SERVER_PRE_CALL_DELAY_S``: seconds to wait once B is dead, before the
  first call. At 6 this reproduced a publish race, since fixed.
- ``DEAD_SERVER_CALL_DELAY_S``: seconds between two calls. From about 1.2 up,
  with B's real backoff, it flipped call 3's error type.

Modes:

- ``single``: two servers. Both are broken until the saga gives up. One is
  revived by ``hangar_start``; the other is called inside its backoff, which is
  refused, and again after it, which revives it.
- ``group``: a one-member group. The member's process is killed between two
  requests: a crash, which a call through the group restarts. Then its upstream
  is broken until the saga gives up: the member leaves rotation and a call
  through the group is refused, until ``hangar_start`` brings it back.
- ``failover``: a two-member group, every threshold at its default. Member B's
  process is killed while its upstream is broken, so every restart fails; then
  twenty calls go through the group. The failed restarts must count against B
  until it leaves rotation, and the group must fail over to A.
- ``defaults``: one server, every health and saga setting at its default but
  the saga's budget; run by
  ``test_the_saga_restarts_a_degraded_server_with_default_settings.py``. Its
  upstream breaks and stays broken. The saga's restarts, 5s then 10s after a
  degrade, come before the server's backoff runs out, about 8s then 16s, so
  the server refuses each first. The harness records what every
  ``StartMcpServerCommand`` came to through a command-bus middleware that only
  watches. ``max_retries`` is 2, not 3: a third restart waits out a 32s
  backoff, and the whole run would outlast the job's 60s timeout.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
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
#: Group and failover modes.
GROUP, MEMBER, MEMBER_B = "pool", "member-a", "member-b"
#: Failover mode: calls through the group once B is dead.
FAILOVER_CALLS = 20
#: Failover mode: B's backoff for the twenty calls, the production cap; see the
#: module docstring.
FAILOVER_BACKOFF_S = 60.0
#: Failover mode's opt-in pacing knobs; see the module docstring.
PRE_CALL_DELAY_ENV = "DEAD_SERVER_PRE_CALL_DELAY_S"
CALL_DELAY_ENV = "DEAD_SERVER_CALL_DELAY_S"
#: The saga's first backoff and retry budget; see the module docstring.
SAGA_BACKOFF_S = 2.5
SAGA_MAX_RETRIES = 1
#: A give-up takes one failed check, 2.5s, and one failed start.
GIVE_UP_DEADLINE_S = 20.0
#: How long the workers run with a server dead, or cold.
QUIET_WINDOW_S = 3.0
#: Defaults mode: the server, and the one saga setting it lowers.
DEFAULTS = "svc-d"
DEFAULTS_MAX_RETRIES = 2
#: Three failing checks, then restarts that each wait out the server's backoff:
#: at most 8.8s after the degrade, then 17.6s after the first failed restart.
DEFAULTS_GIVE_UP_DEADLINE_S = 40.0

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


def _delivered_since(seen: list[list[Any]], mark: int, *entries: list[Any]) -> bool:
    """Every one of ``entries`` reached the watcher after position ``mark``.

    For a command whose events another thread may be delivering. The health and
    GC workers publish whatever a server has pending, so a command can return
    before the events it recorded reach the metrics handler: read straight away,
    the gauge still says what it said before. Waited on as `_saw_dead` is: the
    watcher is subscribed after the metrics handler and the sagas.
    """
    since = seen[mark:]
    return all(entry in since for entry in entries)


def _worker(context: Any, task: str) -> Any:
    return next(w for w in context.background_workers if getattr(w, "task", None) == task)


def _config(mode: str, flags: dict[str, Path]) -> dict[str, Any]:
    servers: dict[str, Any] = {
        server: {"mode": "subprocess", "command": [sys.executable, str(HERE), "upstream", str(flag)]}
        for server, flag in flags.items()
    }
    if mode == "defaults":
        return {"mcp_servers": servers}  # three failed checks degrade it, the default
    if mode == "failover":
        # Every threshold at its default: the case the other modes' settings hid.
        servers[GROUP] = {
            "mode": "group",
            "strategy": "round_robin",
            "min_healthy": 1,
            "members": [{"id": MEMBER}, {"id": MEMBER_B}],
        }
        return {"mcp_servers": servers}
    for spec in servers.values():
        spec["max_consecutive_failures"] = 1
    if mode == "group":
        servers[GROUP] = {
            "mode": "group",
            "strategy": "round_robin",
            "min_healthy": 1,
            # Failures never take the member out: only the give-up may, so the
            # test can see that it did.
            "health": {"unhealthy_threshold": 100, "healthy_threshold": 1},
            "circuit_breaker": {"failure_threshold": 100},
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
    revived_at = len(seen)
    report["revived"] = {BY_START: _tool(client, "hangar_start", {"mcp_server": BY_START})}
    _wait(lambda: _backoff_over(repository.get(BY_CALL).health), GIVE_UP_DEADLINE_S)
    report["revived"][BY_CALL] = _call(client, BY_CALL)
    _wait(lambda: _delivered_since(seen, revived_at, *([server, "started", None] for server in flags)), 10)
    report["after_revival"] = snapshot()

    # Stopped: cold, and not probed while it is.
    stopped_at = len(seen)
    report["stop"] = _tool(client, "hangar_stop", {"mcp_server": BY_START})
    _wait(lambda: _delivered_since(seen, stopped_at, [BY_START, "stopped", "shutdown"]), 10)
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
    started_at = len(seen)
    report["start"] = _tool(client, "hangar_start", {"mcp_server": MEMBER})
    # The group puts the member back on McpServerStarted, which a worker may be
    # the thread delivering.
    _wait(lambda: _delivered_since(seen, started_at, [MEMBER, "started", None]), 10)
    report["after_start"] = _group(client)
    report["start_call"] = _call(client, GROUP)


def _failover(
    client: Any, repository: Any, flags: dict[str, Path], seen: list[list[Any]], report: dict[str, Any]
) -> None:
    report["healthy"] = _group(client)

    # B's process dies, and its upstream will not come back: every restart fails.
    flags[MEMBER_B].touch()
    os.kill(int(Path(f"{flags[MEMBER_B]}.pid").read_text()), signal.SIGKILL)
    _wait(lambda: _saw_dead(seen, MEMBER_B, "crashed"), 10)
    report["crashed"] = _group(client)

    time.sleep(float(os.environ.get(PRE_CALL_DELAY_ENV, "0")))
    spacing = float(os.environ.get(CALL_DELAY_ENV, "0"))

    # B's backoff is fixed for the window, so call 3 lands inside it however
    # far apart the calls are; see the module docstring. Set on B's own
    # tracker, the one the call gate and the start both read, and only once B
    # is dead: the first call must still be free to try a start.
    b_health = repository.get(MEMBER_B).health
    b_health._calculate_backoff = lambda: FAILOVER_BACKOFF_S
    report["calls"] = []
    try:
        for n in range(FAILOVER_CALLS):
            if n:
                time.sleep(spacing)
            report["calls"].append(_call(client, GROUP))
    finally:
        del b_health._calculate_backoff
    report["after"] = _group(client)


def _defaults(
    client: Any, repository: Any, flags: dict[str, Path], seen: list[list[Any]], report: dict[str, Any]
) -> None:
    report["first_call"] = _call(client, DEFAULTS)
    _wait(lambda: [DEFAULTS, "HealthCheckPassed", None] in seen, 10)

    # Broken for good. Three checks fail and degrade it; the saga restarts it
    # until its budget is spent, and gives up.
    flags[DEFAULTS].touch()
    report["broken_mark"] = len(seen)
    _wait(lambda: _give_up_delivered(seen, DEFAULTS), DEFAULTS_GIVE_UP_DEADLINE_S)
    report["dead"] = _snapshot(repository, DEFAULTS)
    report["dead_mark"] = len(seen)

    # Nothing the saga left armed starts it after the give-up.
    time.sleep(QUIET_WINDOW_S)
    report["after_quiet"] = _snapshot(repository, DEFAULTS)


def _watch_starts(command_bus: Any, flags: dict[str, Path], timeline: list[list[Any]]) -> None:
    """Record what every ``StartMcpServerCommand`` for a harness server came to. Watches only."""
    from mcp_hangar.application.commands import StartMcpServerCommand
    from mcp_hangar.domain.exceptions import CannotStartMcpServerError
    from mcp_hangar.infrastructure.command_bus import CommandBusMiddleware

    class Starts(CommandBusMiddleware):
        def __call__(self, command: Any, next_handler: Callable[[Any], Any]) -> Any:
            if not isinstance(command, StartMcpServerCommand) or command.mcp_server_id not in flags:
                return next_handler(command)
            at = time.monotonic()
            try:
                result = next_handler(command)
            except CannotStartMcpServerError:
                timeline.append([command.mcp_server_id, "refused", at])
                raise
            except Exception:
                timeline.append([command.mcp_server_id, "failed", at])
                raise
            timeline.append([command.mcp_server_id, "started", at])
            return result

    command_bus.add_middleware(Starts())


def main(mode: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from starlette.testclient import TestClient

    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import (
        DomainEvent,
        HealthCheckFailed,
        HealthCheckPassed,
        McpServerDegraded,
        McpServerStarted,
        McpServerStateChanged,
        McpServerStopped,
    )
    from mcp_hangar.infrastructure.saga_manager import get_saga_manager
    from mcp_hangar.server.bootstrap import bootstrap, workers
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    workers.HEALTH_CHECK_INTERVAL_SECONDS = 1
    workers.GC_WORKER_INTERVAL_SECONDS = 1

    names = {"group": [MEMBER], "failover": [MEMBER, MEMBER_B], "defaults": [DEFAULTS]}.get(mode, [BY_START, BY_CALL])
    flags = {server: out.parent / f"{server}.down" for server in names}
    context = bootstrap(config_dict=_config(mode, flags))
    recovery = get_saga_manager()._event_sagas["mcp_server_recovery"]
    if mode == "defaults":
        recovery._max_retries = DEFAULTS_MAX_RETRIES
    else:
        recovery._initial_backoff_s = SAGA_BACKOFF_S
        recovery._max_retries = SAGA_MAX_RETRIES

    seen: list[list[Any]] = []
    # When each degrade, give-up and start command happened, on one clock.
    timeline: list[list[Any]] = []
    _watch_starts(context.runtime.command_bus, flags, timeline)

    def observe(event: DomainEvent) -> None:
        server = getattr(event, "mcp_server_id", None)
        if server not in flags:
            return
        if isinstance(event, McpServerStateChanged):
            seen.append([server, "state", event.new_state, event.dead_reason])
            if event.dead_reason == "given_up":
                timeline.append([server, "given_up", time.monotonic()])
        elif isinstance(event, McpServerStarted):
            seen.append([server, "started", None])
        elif isinstance(event, McpServerStopped):
            seen.append([server, "stopped", event.reason])
        elif isinstance(event, McpServerDegraded):
            seen.append([server, "degraded", event.reason])
            timeline.append([server, "degraded", time.monotonic()])
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
        run = {"group": _grouped, "failover": _failover, "defaults": _defaults}.get(mode, _single)
        run(client, repository, flags, seen, report)

    health.stop()
    gc.stop()
    report["events"] = seen
    report["timeline"] = timeline
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
