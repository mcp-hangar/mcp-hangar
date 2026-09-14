"""Bootstrap Hangar, then drive a group through failure, recovery and idle reaping (#1355, #1390).

Run as a script, in its own interpreter, by
``test_a_passing_health_check_returns_a_member_to_rotation.py`` and
``test_scattered_health_check_failures_do_not_open_a_group_circuit.py``:
``python _group_recovery_harness.py <mode> <out.json>``. Not collected by pytest.

A separate process because ``bootstrap()`` fills process-global state -- the
runtime, the saga manager, ``GROUPS`` -- that a second bootstrap in the same
interpreter would inherit.

What runs is production: ``bootstrap()`` with a config dict; the app
``serve --http`` serves, under starlette's ``TestClient``; ``hangar_call``
through the group to ``tests/mock_provider.py`` over stdio; and the background
workers ``bootstrap()`` created, started the way ``ServerLifecycle.start``
starts them. Nothing here builds a saga, registers a member with one, or
publishes an event by hand. Two things are changed, neither on the path under
test: the workers' intervals, 60s and 30s in production, are lowered before
``bootstrap()`` reads them; and ``McpServerGroup.rebalance`` is wrapped to count
its calls, so the test can show that recovery did not come from it.

Modes:

- ``single``: a one-member group, driven out by failing calls, then left to the
  health-check worker.
- ``pair``: the same with two members, where only the first can pass a health
  check. The second is shut down, standing for an upstream that is still down.
- ``idle``: two members with a one-second idle TTL, reaped by the GC worker and
  started again by the next call through the group, several times over.
- ``scattered``: a one-member group whose health checks fail and pass in the
  order ``SCATTERED`` gives. The upstream's ``tools/list``, which is what a
  health check sends, fails while a flag file exists. The harness sets the flag
  for the next check as it hears each one, so the order does not depend on
  timing.

``single`` and ``pair`` also scrape ``mcp_hangar_group_circuit_open`` from the
endpoint ``serve --http`` mounts at ``/metrics`` (#1357). They scrape right after
``bootstrap()``, before any call, and after every call that can move the
circuit. ``single`` then deletes the group and creates it again, through the
commands the group API sends, and scrapes after each.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from typing import Any

MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"
BASE_URL = "http://127.0.0.1:8000"
MODERN_VERSION = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "group-recovery-harness", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

GROUP = "math-pool"
MEMBERS = {"single": ["math-a"], "pair": ["math-a", "math-b"], "idle": ["math-a", "math-b"], "scattered": ["math-a"]}
#: The member whose upstream is healthy again by the time the worker runs.
RECOVERING = "math-a"
#: How long the health worker gets: a pass lands about a second after it starts.
DEADLINE_S = 20.0
#: Idle mode: how many times every member is reaped and started again.
CYCLES = 3
#: Idle mode: how long one round of reaping may take. The TTL is a second and
#: the GC runs every second, so a member is reaped within about two.
REAP_DEADLINE_S = 8.0
#: Scattered mode: the outcome of each health check, in order. Never more than
#: two failures in a row until the last three, and six before them.
SCATTERED = ("fail", "fail", "pass", "fail", "fail", "pass", "fail", "fail", "fail")
#: Scattered mode: a check lands about every second.
SCATTERED_DEADLINE_S = 30.0
#: The gauge ``single`` and ``pair`` scrape (#1357).
METRIC = "mcp_hangar_group_circuit_open"


def _server(mode: str, flag: Path) -> dict[str, Any]:
    spec: dict[str, Any] = {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]}
    if mode == "idle":
        spec["idle_ttl_s"] = 1
    if mode == "scattered":
        spec["env"] = {"MOCK_TOOLS_LIST_FAILS_WHILE": str(flag)}
    return spec


def _config(mode: str, flag: Path) -> dict[str, Any]:
    members = MEMBERS[mode]
    servers: dict[str, Any] = {member: _server(mode, flag) for member in members}
    if mode == "idle":
        servers[GROUP] = {
            "mode": "group",
            # Every call goes to the next member, so both are used and both reaped.
            "strategy": "round_robin",
            "min_healthy": 1,
            # As strict as the group gets: one counted failure takes a member
            # out, two open the circuit. Idle reaping must count as neither.
            "health": {"unhealthy_threshold": 1, "healthy_threshold": 1},
            "circuit_breaker": {"failure_threshold": 2},
            "members": [{"id": member} for member in members],
        }
        return {"mcp_servers": servers}
    if mode == "scattered":
        servers[GROUP] = {
            "mode": "group",
            "strategy": "priority",
            "min_healthy": 1,
            # Only the circuit is under test: two failures in a row neither take
            # the member out of rotation nor degrade the server. Three do both.
            "health": {"unhealthy_threshold": 3, "healthy_threshold": 1},
            "circuit_breaker": {"failure_threshold": 3},
            "members": [{"id": RECOVERING, "priority": 1}],
        }
        return {"mcp_servers": servers}
    servers[GROUP] = {
        "mode": "group",
        "strategy": "priority",
        "min_healthy": 1,
        "health": {"unhealthy_threshold": 2, "healthy_threshold": 1},
        # Open once the last member is driven out, as it was in the live run.
        # The circuit counts failures in a row (#1390), so this is one member's
        # two. In pair mode it also opens when math-a goes out. math-b's start,
        # reported to the group as a success, then closes it (min_healthy 1)
        # and ends the run, and math-b's own two failures open it again. A group
        # circuit has no timer (#1398): it closes only once members are back,
        # so a recovered group is the members' doing.
        "circuit_breaker": {"failure_threshold": 2},
        "members": [{"id": member, "priority": rank} for rank, member in enumerate(members, start=1)],
    }
    return {"mcp_servers": servers}


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


def _call(client: Any, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """``hangar_call`` one tool through the group; the batch result."""
    return _tool(client, "hangar_call", {"calls": [{"mcp_server": GROUP, "tool": tool, "arguments": arguments}]})


def _status(client: Any) -> dict[str, Any]:
    """The group as ``hangar_group_list`` reports it -- what the operator saw."""
    return next(g for g in _tool(client, "hangar_group_list", {})["groups"] if g["group_id"] == GROUP)


def _scrape(metrics: Any) -> list[str]:
    """``GET /metrics``: ``METRIC``'s TYPE line and every sample of it."""
    response = metrics.get("/metrics")
    response.raise_for_status()
    return [
        line
        for line in response.text.splitlines()
        if line == f"# TYPE {METRIC} gauge" or line.startswith(f"{METRIC}{{")
    ]


def _gauge(metrics: Any) -> float | None:
    """The group's sample, or None when there is none."""
    prefix = f'{METRIC}{{group="{GROUP}"}} '
    return next((float(line[len(prefix) :]) for line in _scrape(metrics) if line.startswith(prefix)), None)


def _delete_and_create(context: Any, metrics: Any, report: dict[str, Any]) -> None:
    """Delete the group and create it again, through the commands the group API sends."""
    from mcp_hangar.application.commands.crud_commands import CreateGroupCommand, DeleteGroupCommand

    context.runtime.command_bus.send(DeleteGroupCommand(group_id=GROUP))
    report["metric"]["deleted"] = _scrape(metrics)
    context.runtime.command_bus.send(CreateGroupCommand(group_id=GROUP))
    report["metric"]["created"] = _scrape(metrics)


def _worker(context: Any, task: str) -> Any:
    return next(w for w in context.background_workers if getattr(w, "task", None) == task)


def _recover(
    context: Any, client: Any, metrics: Any, report: dict[str, Any], mode: str, passed: dict[str, int]
) -> None:
    """Fail every member out through calls, then let the health worker run."""
    from mcp_hangar.server.state import GROUPS

    members = MEMBERS[mode]
    report["calls"]["before"] = _call(client, "add", {"a": 1, "b": 2})
    report["metric"]["before"] = _gauge(metrics)
    # Priority routing sends each call to the first member still in rotation,
    # so two failures per member drive every one of them out. After each, the
    # gauge, and the circuit as `hangar_group_list` reports it.
    report["calls"]["failures"] = []
    report["metric"]["failures"] = []
    for _ in range(2 * len(members)):
        report["calls"]["failures"].append(_call(client, "divide", {"a": 1, "b": 0}))
        report["metric"]["failures"].append({"gauge": _gauge(metrics), "circuit_open": _status(client)["circuit_open"]})
    report["status"]["tripped"] = _status(client)
    report["calls"]["refused"] = _call(client, "add", {"a": 1, "b": 2})
    report["metric"]["tripped"] = _gauge(metrics)
    # Whole bodies, for the documented PromQL to run against.
    report["scrapes"] = {"tripped": metrics.get("/metrics").text}

    for member in members:
        if member != RECOVERING:
            context.runtime.repository.get(member).shutdown()

    worker = _worker(context, "health_check")
    worker.start()
    member = GROUPS[GROUP].get_member(RECOVERING)
    deadline = time.monotonic() + DEADLINE_S
    # Until the member is back, or three passes went by without it: enough to
    # say the passes arrive and are not acted on.
    while time.monotonic() < deadline and not member.in_rotation and passed.get(RECOVERING, 0) < 3:
        time.sleep(0.05)
    worker.stop()

    report["status"]["after"] = _status(client)
    report["calls"]["after"] = _call(client, "add", {"a": 1, "b": 2})
    report["metric"]["after"] = _gauge(metrics)
    report["scrapes"]["recovered"] = metrics.get("/metrics").text
    if mode == "single":
        _delete_and_create(context, metrics, report)


def _idle(context: Any, client: Any, report: dict[str, Any], stopped: list[list[str]]) -> None:
    """Use every member, let the GC worker reap them all, and go round again."""
    members = MEMBERS["idle"]

    def idle_stops() -> int:
        return sum(1 for _member, reason in stopped if reason == "idle")

    worker = _worker(context, "gc")
    worker.start()
    report["cycles"] = []
    for cycle in range(1, CYCLES + 1):
        # One call per member; each starts its member if the GC reaped it.
        calls = [_call(client, "add", {"a": 1, "b": 2}) for _ in members]
        deadline = time.monotonic() + REAP_DEADLINE_S
        while time.monotonic() < deadline and idle_stops() < cycle * len(members):
            time.sleep(0.05)
        report["cycles"].append({"calls": calls, "idle_stops": idle_stops(), "status": _status(client)})
    worker.stop()


def _steer(flag: Path, check: int) -> None:
    """Make health check number ``check`` (from 0) fail or pass, as ``SCATTERED`` says."""
    if check < len(SCATTERED) and SCATTERED[check] == "fail":
        flag.touch()
    else:
        flag.unlink(missing_ok=True)


def _after_check(passed: bool) -> dict[str, Any]:
    """The group as one health check left it. The saga reported the check before this runs."""
    from mcp_hangar.server.state import GROUPS

    group = GROUPS[GROUP]
    return {
        "passed": passed,
        "circuit_open": group.circuit_open,
        "circuit_failures": group._circuit_breaker.failure_count,
        "in_rotation": group.get_member(RECOVERING).in_rotation,
    }


def _scattered(context: Any, client: Any, report: dict[str, Any], checks: list[dict[str, Any]], flag: Path) -> None:
    """Start the member with a call, then let the health worker run the checks ``SCATTERED`` orders."""
    report["calls"]["before"] = _call(client, "add", {"a": 1, "b": 2})
    _steer(flag, 0)
    worker = _worker(context, "health_check")
    worker.start()
    deadline = time.monotonic() + SCATTERED_DEADLINE_S
    while time.monotonic() < deadline and len(checks) < len(SCATTERED):
        time.sleep(0.05)
    worker.stop()
    report["pattern"] = list(SCATTERED)
    report["checks"] = list(checks)


def main(mode: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import DomainEvent, HealthCheckFailed, HealthCheckPassed, McpServerStopped
    from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
    from mcp_hangar.server.bootstrap import bootstrap, workers
    from mcp_hangar.server.lifecycle import mcp_app_for_serving, metrics_endpoint

    workers.HEALTH_CHECK_INTERVAL_SECONDS = 1
    workers.GC_WORKER_INTERVAL_SECONDS = 1

    rebalances: list[str] = []
    rebalance = McpServerGroup.rebalance

    def counted_rebalance(self: McpServerGroup) -> None:
        rebalances.append(str(self.id))
        rebalance(self)

    McpServerGroup.rebalance = counted_rebalance  # type: ignore[method-assign]

    flag = out.parent / "tools-list-fails"
    context = bootstrap(config_dict=_config(mode, flag))

    passed: dict[str, int] = {}
    stopped: list[list[str]] = []
    checks: list[dict[str, Any]] = []

    def observe(event: DomainEvent) -> None:
        if isinstance(event, HealthCheckPassed):
            passed[event.mcp_server_id] = passed.get(event.mcp_server_id, 0) + 1
        elif isinstance(event, McpServerStopped):
            stopped.append([event.mcp_server_id, event.reason])
        if mode == "scattered" and isinstance(event, (HealthCheckPassed, HealthCheckFailed)):
            checks.append(_after_check(isinstance(event, HealthCheckPassed)))
            _steer(flag, len(checks))

    # Watches only. Subscribed after the saga manager, so it hears an event
    # after the saga has.
    context.runtime.event_bus.subscribe_to_all(observe, kind=HandlerKind.PROJECTION)

    # The endpoint `serve --http` mounts at /metrics, scraped from this process.
    metrics = TestClient(Starlette(routes=[Route("/metrics", metrics_endpoint, methods=["GET"])]), base_url=BASE_URL)
    # Before any call: a loaded group is on the scrape before its first transition.
    report: dict[str, Any] = {"calls": {}, "status": {}, "metric": {"boot": _scrape(metrics)}}
    with TestClient(mcp_app_for_serving(context.mcp_server), base_url=BASE_URL) as client:
        if mode == "idle":
            _idle(context, client, report, stopped)
        elif mode == "scattered":
            _scattered(context, client, report, checks, flag)
        else:
            _recover(context, client, metrics, report, mode, passed)

    # Taken before the servers below are shut down, which is not under test.
    report["health_checks_passed"] = dict(passed)
    report["stops"] = list(stopped)
    report["rebalances"] = rebalances
    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
