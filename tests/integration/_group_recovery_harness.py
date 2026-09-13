"""Bootstrap Hangar, then drive a group through failure, recovery and idle reaping (#1355).

Run as a script, in its own interpreter, by
``test_a_passing_health_check_returns_a_member_to_rotation.py``:
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
MEMBERS = {"single": ["math-a"], "pair": ["math-a", "math-b"], "idle": ["math-a", "math-b"]}
#: The member whose upstream is healthy again by the time the worker runs.
RECOVERING = "math-a"
#: How long the health worker gets: a pass lands about a second after it starts.
DEADLINE_S = 20.0
#: Idle mode: how many times every member is reaped and started again.
CYCLES = 3
#: Idle mode: how long one round of reaping may take. The TTL is a second and
#: the GC runs every second, so a member is reaped within about two.
REAP_DEADLINE_S = 8.0


def _server(mode: str) -> dict[str, Any]:
    spec: dict[str, Any] = {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]}
    if mode == "idle":
        spec["idle_ttl_s"] = 1
    return spec


def _config(mode: str) -> dict[str, Any]:
    members = MEMBERS[mode]
    servers: dict[str, Any] = {member: _server(mode) for member in members}
    if mode == "idle":
        servers[GROUP] = {
            "mode": "group",
            # Every call goes to the next member, so both are used and both reaped.
            "strategy": "round_robin",
            "min_healthy": 1,
            # As strict as the group gets: one counted failure takes a member
            # out, two open the circuit. Idle reaping must count as neither.
            "health": {"unhealthy_threshold": 1, "healthy_threshold": 1},
            "circuit_breaker": {"failure_threshold": 2, "reset_timeout_s": 3600},
            "members": [{"id": member} for member in members],
        }
        return {"mcp_servers": servers}
    servers[GROUP] = {
        "mode": "group",
        "strategy": "priority",
        "min_healthy": 1,
        "health": {"unhealthy_threshold": 2, "healthy_threshold": 1},
        # Opens on the last failure that drives the last member out, as it had
        # in the live run. And it cannot close by time within this run, so a
        # recovered group is not the reset timeout's doing.
        "circuit_breaker": {"failure_threshold": 2 * len(members), "reset_timeout_s": 3600},
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


def _worker(context: Any, task: str) -> Any:
    return next(w for w in context.background_workers if getattr(w, "task", None) == task)


def _recover(context: Any, client: Any, report: dict[str, Any], mode: str, passed: dict[str, int]) -> None:
    """Fail every member out through calls, then let the health worker run."""
    from mcp_hangar.server.state import GROUPS

    members = MEMBERS[mode]
    report["calls"]["before"] = _call(client, "add", {"a": 1, "b": 2})
    # Priority routing sends each call to the first member still in rotation,
    # so two failures per member drive every one of them out.
    report["calls"]["failures"] = [_call(client, "divide", {"a": 1, "b": 0}) for _ in range(2 * len(members))]
    report["status"]["tripped"] = _status(client)
    report["calls"]["refused"] = _call(client, "add", {"a": 1, "b": 2})

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


def main(mode: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from starlette.testclient import TestClient

    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import DomainEvent, HealthCheckPassed, McpServerStopped
    from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
    from mcp_hangar.server.bootstrap import bootstrap, workers
    from mcp_hangar.server.lifecycle import mcp_app_for_serving

    workers.HEALTH_CHECK_INTERVAL_SECONDS = 1
    workers.GC_WORKER_INTERVAL_SECONDS = 1

    rebalances: list[str] = []
    rebalance = McpServerGroup.rebalance

    def counted_rebalance(self: McpServerGroup) -> None:
        rebalances.append(str(self.id))
        rebalance(self)

    McpServerGroup.rebalance = counted_rebalance  # type: ignore[method-assign]

    context = bootstrap(config_dict=_config(mode))

    passed: dict[str, int] = {}
    stopped: list[list[str]] = []

    def observe(event: DomainEvent) -> None:
        if isinstance(event, HealthCheckPassed):
            passed[event.mcp_server_id] = passed.get(event.mcp_server_id, 0) + 1
        elif isinstance(event, McpServerStopped):
            stopped.append([event.mcp_server_id, event.reason])

    # Watches only. Subscribed after the saga manager, so it hears an event
    # after the saga has.
    context.runtime.event_bus.subscribe_to_all(observe, kind=HandlerKind.PROJECTION)

    report: dict[str, Any] = {"calls": {}, "status": {}}
    with TestClient(mcp_app_for_serving(context.mcp_server), base_url=BASE_URL) as client:
        if mode == "idle":
            _idle(context, client, report, stopped)
        else:
            _recover(context, client, report, mode, passed)

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
