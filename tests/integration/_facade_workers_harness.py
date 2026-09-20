"""Run the facade on the real `bootstrap()` and report what its background workers did (#1435).

Run as a script, in its own interpreter, by
``test_the_facade_runs_the_background_workers.py``:
``python _facade_workers_harness.py <sync|async> <out.json>``. Not collected by
pytest. A separate process because ``bootstrap()`` fills process-global state,
and two boots in one interpreter would read each other's.

Two servers from ``tests/mock_provider.py`` over stdio, each started by one
call: ``idle``, with a one-second ``idle_ttl_s``, for the GC worker to stop, and
``steady``, with the default, for the health-check worker to check. One thing
is changed, and it is not on the path under test: the workers' intervals, 30s
and 60s in production, are lowered to a second before ``bootstrap()`` reads
them.

* ``sync``: ``SyncHangar.from_builder``, started twice and stopped twice.
* ``async``: ``Hangar.from_config`` on a file, which also builds the config
  reload worker; ``async with``, a second ``start()`` inside it and a second
  ``stop()`` after it.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"
COMMAND = [sys.executable, str(MOCK_PROVIDER)]
IDLE, STEADY = "idle", "steady"
#: The names of the threads the background workers run on.
WORKER_THREADS = frozenset({"worker-gc", "worker-health_check", "worker-metrics-snapshot", "config-reload-poller"})
DEADLINE_S = 30.0
#: How long a thread started under the facade may take to end once it is stopped.
GRACE_S = 5.0


class Watch:
    """What the workers did, read from the events they publish."""

    def __init__(self) -> None:
        self.stops: list[list[str]] = []
        self.health_checks: dict[str, int] = {}

    def attach(self, context: Any) -> None:
        from mcp_hangar.domain.contracts.event_bus import HandlerKind
        from mcp_hangar.domain.events import DomainEvent, HealthCheckPassed, McpServerStopped

        def observe(event: DomainEvent) -> None:
            if isinstance(event, HealthCheckPassed):
                self.health_checks[event.mcp_server_id] = self.health_checks.get(event.mcp_server_id, 0) + 1
            elif isinstance(event, McpServerStopped):
                self.stops.append([event.mcp_server_id, event.reason])

        context.runtime.event_bus.subscribe_to_all(observe, kind=HandlerKind.PROJECTION)

    def done(self) -> bool:
        return [IDLE, "idle"] in self.stops and self.health_checks.get(STEADY, 0) >= 1


def _worker_threads() -> list[str]:
    return sorted(thread.name for thread in threading.enumerate() if thread.name in WORKER_THREADS)


def _left_running(before: set[threading.Thread]) -> list[str]:
    """Every thread started since *before* that is still running once the grace period is over."""
    deadline = time.monotonic() + GRACE_S
    left = []
    for thread in threading.enumerate():
        if thread in before or thread is threading.current_thread():
            continue
        thread.join(max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            left.append(thread.name)
    return sorted(left)


def _wait_for(watch: Watch) -> None:
    deadline = time.monotonic() + DEADLINE_S
    while time.monotonic() < deadline and not watch.done():
        time.sleep(0.05)


def _started(context: Any) -> dict[str, Any]:
    """The workers bootstrap built, the ones that can run, and the ones running.

    The config reload worker is built either way and disabled without a config
    file, under `serve` as under the facade.
    """
    workers = context.background_workers
    return {
        "built": sorted(worker.task for worker in workers),
        "enabled": sorted(worker.task for worker in workers if getattr(worker, "_enabled", True)),
        "running": sorted(worker.task for worker in workers if worker.running),
    }


def _sync(out: Path) -> dict[str, Any]:
    from mcp_hangar.facade import HangarConfig, SyncHangar

    config = (
        HangarConfig()
        .add_mcp_server(IDLE, command=COMMAND, idle_ttl_s=1)
        .add_mcp_server(STEADY, command=COMMAND)
        .build()
    )
    before = set(threading.enumerate())
    hangar = SyncHangar.from_builder(config)
    hangar.start()
    threads_after_start = _worker_threads()
    hangar.start()  # a second start does nothing
    threads_after_second_start = _worker_threads()

    context = hangar._hangar._context
    assert context is not None
    report = _started(context)
    watch = Watch()
    watch.attach(context)

    hangar.invoke(IDLE, "add", {"a": 1, "b": 2})
    hangar.invoke(STEADY, "add", {"a": 1, "b": 2})
    _wait_for(watch)
    idle_state = hangar.get_mcp_server(IDLE).state

    workers = list(context.background_workers)
    hangar.stop()
    hangar.stop()  # a second stop does nothing

    return {
        **report,
        "threads_after_start": threads_after_start,
        "threads_after_second_start": threads_after_second_start,
        "stops": watch.stops,
        "health_checks": watch.health_checks,
        "idle_state": idle_state,
        "running_after_stop": sorted(worker.task for worker in workers if worker.running),
        "worker_threads_after_stop": _worker_threads(),
        "left_running": _left_running(before),
    }


async def _async_run(config_path: Path) -> dict[str, Any]:
    from mcp_hangar.facade import Hangar

    hangar = Hangar.from_config(config_path)
    async with hangar:
        threads_after_start = _worker_threads()
        await hangar.start()  # a second start does nothing
        threads_after_second_start = _worker_threads()

        context = hangar._context
        assert context is not None
        report = _started(context)
        watch = Watch()
        watch.attach(context)

        await hangar.invoke(IDLE, "add", {"a": 1, "b": 2})
        await hangar.invoke(STEADY, "add", {"a": 1, "b": 2})
        await asyncio.to_thread(_wait_for, watch)
        idle_state = (await hangar.get_mcp_server(IDLE)).state
        workers = list(context.background_workers)
    await hangar.stop()  # a second stop does nothing

    return {
        **report,
        "threads_after_start": threads_after_start,
        "threads_after_second_start": threads_after_second_start,
        "stops": watch.stops,
        "health_checks": watch.health_checks,
        "idle_state": idle_state,
        "running_after_stop": sorted(worker.task for worker in workers if worker.running),
        "worker_threads_after_stop": _worker_threads(),
    }


def _async(out: Path) -> dict[str, Any]:
    config_path = out.parent / "config.yaml"
    # JSON is YAML.
    config_path.write_text(
        json.dumps(
            {
                "mcp_servers": {
                    IDLE: {"mode": "subprocess", "command": COMMAND, "idle_ttl_s": 1},
                    STEADY: {"mode": "subprocess", "command": COMMAND},
                }
            }
        )
    )
    # Read once `asyncio.run` has returned, so its own executor thread, which
    # `asyncio.to_thread` above started, has been shut down and is not counted.
    before = set(threading.enumerate())
    report = asyncio.run(_async_run(config_path))
    return {**report, "left_running": _left_running(before)}


MODES = {"sync": _sync, "async": _async}


def main(mode: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data

    from mcp_hangar.server.bootstrap import workers

    workers.HEALTH_CHECK_INTERVAL_SECONDS = 1
    workers.GC_WORKER_INTERVAL_SECONDS = 1

    report = MODES[mode](out)
    out.write_text(json.dumps(report, default=str))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
