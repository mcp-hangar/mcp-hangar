"""Boot a front door with a required catalogue, break its upstreams, and ask it whether it is ready (#1446).

Run as a script, in its own interpreter, by
``test_a_front_door_is_ready_once_its_catalogue_is_projected.py``:
``python _catalogue_readiness_harness.py <mode> <out.json>``. Not collected by
pytest.

A separate process because ``bootstrap()`` fills process-global state -- the
runtime, the saga manager, the metrics registry, the required catalogue -- that
a second bootstrap in the same interpreter would inherit.

What runs is production: ``bootstrap()`` with a config dict, then
``ServerLifecycle.start()`` -- the workers, and the front door's warm-up and
catalogue retry on the thread it starts -- and ``ServerLifecycle.shutdown()``.
The upstreams are real processes: ``_dead_server_harness.py upstream <flag>``,
which will not start while its flag file exists, and
``tests/undeclared_tool_provider.py``, which serves a tool its server does not
declare. ``/health/ready`` is answered by ``build_readiness_report`` over the
runtime's repository, which is the whole of ``run_http``'s readiness endpoint,
and ``/metrics`` by the endpoint ``serve --http`` mounts, both under starlette's
``TestClient``. Nothing here sends a start or publishes an event.

Changed, and none of it on the path under test: the workers' intervals are
lowered, as the other harnesses lower them; in ``blocked`` mode the recovery
saga gets one restart after 2.5s, as ``_dead_server_harness.py`` gives it, and
``svc-down``'s backoff is pinned to half a second, so the retry is seen
attempting it several times in the window where it must leave the others alone.

Modes:

- ``recover``: ``svc-idle`` serves and has a one-second idle TTL. ``svc-late``
  is down at boot and comes up once the retry has failed on it. The replica is
  not ready until the retry projects ``svc-late``, and ``svc-idle``, stopped for
  being idle meanwhile, is never started again. Then ``svc-late`` crashes, and
  the replica stays ready.
- ``blocked``: ``svc-blocked`` is capability-blocked at boot, and ``svc-given-up``
  is down until the recovery saga gives up on it, then comes back. The retry
  must start neither; it keeps working on ``svc-down``, which stays down. Then
  shutdown, which must stop it.
- ``egress``: ``recover``'s configuration without ``tool_access.mode``. Nothing
  is started, and readiness is today's.
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
UPSTREAM = HERE.with_name("_dead_server_harness.py")
UNDECLARED = HERE.parents[1] / "undeclared_tool_provider.py"
BASE_URL = "http://127.0.0.1:8000"

IDLE, LATE = "svc-idle", "svc-late"
BLOCKED, GIVEN_UP, DOWN = "svc-blocked", "svc-given-up", "svc-down"
#: How long a mode waits for what it waits for.
DEADLINE_S = 25.0
#: How long the workers and the retry run while the harness only watches.
QUIET_WINDOW_S = 4.0
#: `blocked` mode: the saga's settings, as `_dead_server_harness.py` sets them.
SAGA_BACKOFF_S = 2.5
SAGA_MAX_RETRIES = 1
#: `blocked` mode: svc-down's backoff, so the retry is due on it often.
DOWN_BACKOFF_S = 0.5


def _upstream(flag: Path) -> dict[str, Any]:
    return {"mode": "subprocess", "command": [sys.executable, str(UPSTREAM), "upstream", str(flag)]}


def _config(mode: str, work: Path) -> dict[str, Any]:
    if mode == "blocked":
        servers: dict[str, Any] = {
            BLOCKED: {
                "mode": "subprocess",
                "command": [sys.executable, str(UNDECLARED)],
                "env": {"UNDECLARED_PROVIDER_RECORD": str(work / "blocked.jsonl")},
                "capabilities": {"tools": {"expected_tools": ["add"]}, "enforcement_mode": "block"},
            },
            # One failed start degrades it, so the saga has it from the first.
            GIVEN_UP: {**_upstream(work / f"{GIVEN_UP}.down"), "max_consecutive_failures": 1},
            # Never degrades: dead for a failed start, which the retry owns.
            DOWN: {**_upstream(work / f"{DOWN}.down"), "max_consecutive_failures": 1000},
        }
    else:
        servers = {
            IDLE: {**_upstream(work / f"{IDLE}.down"), "idle_ttl_s": 1},
            # Not degraded by the failures the test causes, so it stays the retry's.
            LATE: {**_upstream(work / f"{LATE}.down"), "max_consecutive_failures": 1000},
        }
    tool_access: dict[str, Any] = {"required_catalogue": {"servers": list(servers), "retry_for_s": 120}}
    if mode != "egress":
        tool_access["mode"] = "front_door"
    return {"mcp_servers": servers, "tool_access": tool_access}


def _wait(condition: Callable[[], bool], deadline_s: float = DEADLINE_S) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


class Probe:
    """``/health/ready`` and ``/metrics`` over HTTP, and what the event bus carried."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.seen: list[list[Any]] = []

    def ready(self) -> dict[str, Any]:
        response = self.client.get("/health/ready")
        return {"status": response.status_code, "body": response.json()}

    def retries(self, server: str) -> dict[str, float]:
        """``mcp_hangar_catalogue_retries_total`` for ``server``, by outcome."""
        out: dict[str, float] = {}
        for line in self.client.get("/metrics").text.splitlines():
            if line.startswith("mcp_hangar_catalogue_retries_total{") and f'mcp_server="{server}"' in line:
                outcome = line.split('outcome="', 1)[1].split('"', 1)[0]
                out[outcome] = float(line.split()[-1])
        return out

    def attempts(self, server: str) -> float:
        return sum(self.retries(server).values())

    def events(self, server: str) -> list[list[Any]]:
        return [event[1:] for event in self.seen if event[0] == server]

    def starts(self, server: str) -> int:
        return sum(1 for event in self.events(server) if event == ["started"])


def _state(repository: Any, server: str) -> list[Any]:
    aggregate = repository.get(server)
    return [aggregate.state.value, aggregate.dead_reason_snapshot]


def _recover(probe: Probe, repository: Any, work: Path, lifecycle: Any, report: dict[str, Any]) -> None:
    from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry

    registry = get_tool_projection_registry()
    _wait(lambda: registry.was_projected(IDLE) and probe.ready()["body"].get("catalogue", {}).get("retry") == "running")
    report["boot"] = probe.ready()

    # The retry fails on svc-late at least once, and the GC stops svc-idle.
    _wait(lambda: probe.retries(LATE).get("failed", 0) >= 1 and [IDLE, "stopped", "idle"] in probe.seen)
    report["while_late"] = {
        "ready": probe.ready(),
        "idle_state": _state(repository, IDLE),
        "idle_stops": probe.events(IDLE).count(["stopped", "idle"]),
    }

    # svc-late's upstream comes back. Nothing but the retry starts it.
    (work / f"{LATE}.down").unlink()
    _wait(lambda: probe.ready()["status"] == 200)
    report["recovered"] = {"ready": probe.ready(), "late_retries": probe.retries(LATE)}

    # A later outage: svc-late's process dies and will not come back.
    (work / f"{LATE}.down").touch()
    os.kill(int(Path(f"{work / LATE}.down.pid").read_text()), signal.SIGKILL)
    _wait(lambda: _state(repository, LATE)[0] != "ready")
    time.sleep(QUIET_WINDOW_S)
    report["after_outage"] = {
        "ready": probe.ready(),
        "late_state": _state(repository, LATE),
        "idle_state": _state(repository, IDLE),
    }
    report["idle_starts"] = probe.starts(IDLE)
    report["idle_retries"] = probe.retries(IDLE)
    lifecycle.shutdown()


def _blocked(probe: Probe, repository: Any, work: Path, lifecycle: Any, report: dict[str, Any]) -> None:
    _wait(
        lambda: (
            ["state", "dead", "capability_blocked"] in probe.events(BLOCKED)
            and ["state", "dead", "given_up"] in probe.events(GIVEN_UP)
        )
    )
    report["dead"] = {server: _state(repository, server) for server in (BLOCKED, GIVEN_UP)}

    # svc-given-up's upstream is back: a start would now succeed.
    (work / f"{GIVEN_UP}.down").unlink()
    down_before = probe.attempts(DOWN)
    time.sleep(QUIET_WINDOW_S)
    report["window"] = {
        "ready": probe.ready(),
        "states": {server: _state(repository, server) for server in (BLOCKED, GIVEN_UP, DOWN)},
        "down_attempts": probe.attempts(DOWN) - down_before,
    }
    report["retries"] = {server: probe.retries(server) for server in (BLOCKED, GIVEN_UP)}
    report["given_up_starts"] = probe.starts(GIVEN_UP)
    report["given_up_ever_served"] = Path(f"{work / GIVEN_UP}.down.pid").exists()
    record = work / "blocked.jsonl"
    entries = [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []
    report["blocked_launches"] = sum(1 for entry in entries if entry["event"] == "start")

    lifecycle.shutdown()
    at_shutdown = probe.attempts(DOWN)
    time.sleep(QUIET_WINDOW_S)
    report["after_shutdown"] = {"ready": probe.ready(), "down_attempts": probe.attempts(DOWN) - at_shutdown}


def _egress(probe: Probe, repository: Any, work: Path, lifecycle: Any, report: dict[str, Any]) -> None:
    report["boot"] = probe.ready()
    time.sleep(QUIET_WINDOW_S)
    report["later"] = probe.ready()
    report["states"] = {server: _state(repository, server) for server in (IDLE, LATE)}
    report["starts"] = {server: probe.starts(server) for server in (IDLE, LATE)}
    report["retries"] = {server: probe.retries(server) for server in (IDLE, LATE)}
    report["ever_served"] = {server: Path(f"{work / server}.down.pid").exists() for server in (IDLE, LATE)}
    lifecycle.shutdown()


def main(mode: str, out: Path) -> None:
    work = out.parent
    os.chdir(work)  # bootstrap keeps its data under ./data

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from mcp_hangar.domain.contracts.event_bus import HandlerKind
    from mcp_hangar.domain.events import DomainEvent, McpServerStarted, McpServerStateChanged, McpServerStopped
    from mcp_hangar.infrastructure.saga_manager import get_saga_manager
    from mcp_hangar.server.bootstrap import bootstrap, workers
    from mcp_hangar.server.bootstrap.composition import get_runtime
    from mcp_hangar.server.lifecycle import build_readiness_report, metrics_endpoint, ServerLifecycle

    workers.HEALTH_CHECK_INTERVAL_SECONDS = 1
    workers.GC_WORKER_INTERVAL_SECONDS = 1

    config = _config(mode, work)
    # Down from the start: every upstream but svc-idle and svc-blocked.
    for server, spec in config["mcp_servers"].items():
        if server not in (IDLE, BLOCKED):
            Path(spec["command"][-1]).touch()

    context = bootstrap(config_dict=config)
    if mode == "blocked":
        recovery = get_saga_manager()._event_sagas["mcp_server_recovery"]
        recovery._initial_backoff_s = SAGA_BACKOFF_S
        recovery._max_retries = SAGA_MAX_RETRIES
        context.runtime.repository.get(DOWN).health._calculate_backoff = lambda: DOWN_BACKOFF_S

    def readiness(request: Any) -> JSONResponse:
        # `run_http`'s readiness endpoint, all of it.
        body, status_code = build_readiness_report(get_runtime().repository)
        return JSONResponse(body, status_code=status_code)

    app = Starlette(
        routes=[
            Route("/health/ready", readiness, methods=["GET"]),
            Route("/metrics", metrics_endpoint, methods=["GET"]),
        ]
    )
    probe = Probe(TestClient(app, base_url=BASE_URL))

    def observe(event: DomainEvent) -> None:
        server = getattr(event, "mcp_server_id", None)
        if server not in config["mcp_servers"]:
            return
        if isinstance(event, McpServerStateChanged):
            probe.seen.append([server, "state", event.new_state, event.dead_reason])
        elif isinstance(event, McpServerStarted):
            probe.seen.append([server, "started"])
        elif isinstance(event, McpServerStopped):
            probe.seen.append([server, "stopped", event.reason])

    # Watches only. Subscribed after the saga manager, so it hears an event
    # after the saga has.
    context.runtime.event_bus.subscribe_to_all(observe, kind=HandlerKind.PROJECTION)

    lifecycle = ServerLifecycle(context)
    lifecycle.start()
    report: dict[str, Any] = {}
    run = {"recover": _recover, "blocked": _blocked, "egress": _egress}[mode]
    run(probe, context.runtime.repository, work, lifecycle, report)

    report["events"] = probe.seen
    out.write_text(json.dumps(report))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
