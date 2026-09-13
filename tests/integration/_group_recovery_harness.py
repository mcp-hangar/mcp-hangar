"""Bootstrap Hangar, drive a group's members out of rotation, and let the health worker heal it (#1355).

Run as a script, in its own interpreter, by
``test_a_passing_health_check_returns_a_member_to_rotation.py``:
``python _group_recovery_harness.py <mode> <out.json>``. Not collected by pytest.

A separate process because ``bootstrap()`` fills process-global state -- the
runtime, the saga manager, ``GROUPS`` -- that a second bootstrap in the same
interpreter would inherit.

What runs is production: ``bootstrap()`` with a config dict; the app
``serve --http`` serves, under starlette's ``TestClient``; ``hangar_call``
through the group to ``tests/mock_provider.py`` over stdio; and the health-check
worker ``bootstrap()`` created, started the way ``ServerLifecycle.start`` starts
it. Nothing here builds a saga, registers a member with one, or publishes an
event by hand. Two things are changed, neither on the path under test: the
worker's interval, 60s in production, is lowered before ``bootstrap()`` reads
it; and ``McpServerGroup.rebalance`` is wrapped to count its calls, so the test
can show that recovery did not come from it.

Modes: ``single`` is a one-member group. ``pair`` has two members, both driven
out, and only the first can pass a health check: the second is shut down,
standing for an upstream that is still down.
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
MEMBERS = {"single": ["math-a"], "pair": ["math-a", "math-b"]}
#: The member whose upstream is healthy again by the time the worker runs.
RECOVERING = "math-a"
#: How long the worker gets: a pass lands about a second after it starts.
DEADLINE_S = 20.0


def _config(mode: str) -> dict[str, Any]:
    members = MEMBERS[mode]
    servers: dict[str, Any] = {
        member: {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]} for member in members
    }
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


def main(mode: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from starlette.testclient import TestClient

    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import DomainEvent, HealthCheckPassed
    from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
    from mcp_hangar.server.bootstrap import bootstrap, workers
    from mcp_hangar.server.lifecycle import mcp_app_for_serving
    from mcp_hangar.server.state import GROUPS

    workers.HEALTH_CHECK_INTERVAL_SECONDS = 1

    rebalances: list[str] = []
    rebalance = McpServerGroup.rebalance

    def counted_rebalance(self: McpServerGroup) -> None:
        rebalances.append(str(self.id))
        rebalance(self)

    McpServerGroup.rebalance = counted_rebalance  # type: ignore[method-assign]

    members = MEMBERS[mode]
    context = bootstrap(config_dict=_config(mode))

    passed: dict[str, int] = {}

    def observe(event: DomainEvent) -> None:
        if isinstance(event, HealthCheckPassed):
            passed[event.mcp_server_id] = passed.get(event.mcp_server_id, 0) + 1

    # Watches only. Subscribed after the saga manager, so it hears a pass after
    # the saga has.
    context.runtime.event_bus.subscribe_to_all(observe, kind=HandlerKind.PROJECTION)

    report: dict[str, Any] = {"calls": {}, "status": {}}
    with TestClient(mcp_app_for_serving(context.mcp_server), base_url=BASE_URL) as client:
        report["calls"]["before"] = _call(client, "add", {"a": 1, "b": 2})
        # Priority routing sends each call to the first member still in
        # rotation, so two failures per member drive every one of them out.
        report["calls"]["failures"] = [_call(client, "divide", {"a": 1, "b": 0}) for _ in range(2 * len(members))]
        report["status"]["tripped"] = _status(client)
        report["calls"]["refused"] = _call(client, "add", {"a": 1, "b": 2})

        for member in members:
            if member != RECOVERING:
                context.runtime.repository.get(member).shutdown()

        worker = next(w for w in context.background_workers if getattr(w, "task", None) == "health_check")
        worker.start()
        member = GROUPS[GROUP].get_member(RECOVERING)
        deadline = time.monotonic() + DEADLINE_S
        # Until the member is back, or three passes went by without it: enough
        # to say the passes arrive and are not acted on.
        while time.monotonic() < deadline and not member.in_rotation and passed.get(RECOVERING, 0) < 3:
            time.sleep(0.05)
        worker.stop()

        report["health_checks_passed"] = dict(passed)
        report["status"]["after"] = _status(client)
        report["calls"]["after"] = _call(client, "add", {"a": 1, "b": 2})

    report["rebalances"] = rebalances
    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker thread is a daemon mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
