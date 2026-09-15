"""Run the facade on the real `bootstrap()` and report what it started besides the workers (#1465).

Run as a script, in its own interpreter, by
``test_the_facade_starts_coordination_and_the_warm_up.py``:
``python _facade_coordination_harness.py <coordination|front_door> <out.json>``.
Not collected by pytest. A separate process because ``bootstrap()`` fills
process-global state -- the lease keeper, the event tailer, the required
catalogue -- that a second boot in the same interpreter would inherit.

Both modes start the facade twice at once and count the boots, by wrapping
``bootstrap`` where the facade looks it up. Nothing else is wrapped except as
said per mode, and nothing sends a start or publishes an event.

* ``coordination``: ``Hangar.from_config`` on a file with a ``coordination:``
  block over the SQLite backend, and two concurrent ``start()`` calls. SQLite is
  local to one process, so bootstrap builds no keeper for it; the backend class
  is marked shareable first, as ``test_management_stops_when_the_lease_does.py``
  marks it, which is the one change here. The lease's renew interval is lowered
  to a fifth of a second, in the configuration. The tailer's ``tick`` is
  wrapped to count its reads.
* ``front_door``: ``SyncHangar.from_config`` on a front-door file, started from
  two threads at once. ``warm`` is ``tests/mock_provider.py`` over stdio;
  ``down`` exits as it starts, and fails every start, so the
  required-catalogue retry is still attempting it when ``stop()`` comes. Its
  ``max_consecutive_failures`` keeps it dead for a failed start, which is the
  retry's to start, rather than degraded, which is the recovery saga's.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"
WARM, DOWN = "warm", "down"
#: The names of the threads coordination and the warm-up run on.
COORDINATION_THREADS = frozenset({"management-lease", "event-tailer"})
WARM_UP_THREAD = "mcp-hangar-front-door-warmup"
DEADLINE_S = 25.0
#: How long a thread started under the facade may take to end once it is stopped.
GRACE_S = 5.0


def _threads(names: frozenset[str]) -> list[str]:
    return sorted(thread.name for thread in threading.enumerate() if thread.name in names)


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


def _wait(condition: Any) -> bool:
    deadline = time.monotonic() + DEADLINE_S
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def _count_boots() -> list[int]:
    """Count calls to `bootstrap`, which the facade imports from its package at each start."""
    import importlib

    # The package, not the `bootstrap` function `mcp_hangar.server` re-exports under the same name.
    package = importlib.import_module("mcp_hangar.server.bootstrap")

    boots: list[int] = []
    real = package.bootstrap

    def counted(*args: Any, **kwargs: Any) -> Any:
        boots.append(1)
        return real(*args, **kwargs)

    package.bootstrap = counted  # type: ignore[assignment]
    return boots


async def _coordination_run(config_path: Path, boots: list[int]) -> dict[str, Any]:
    from mcp_hangar.facade import Hangar
    from mcp_hangar.server.bootstrap.coordination import get_event_tailer, get_lease_keeper, may_manage

    hangar = Hangar.from_config(config_path)
    await asyncio.gather(hangar.start(), hangar.start())

    keeper, tailer = get_lease_keeper(), get_event_tailer()
    assert keeper is not None and tailer is not None
    ticks: list[int] = []
    tick = tailer.tick

    def counted_tick() -> int:
        ticks.append(1)
        return tick()

    tailer.tick = counted_tick  # type: ignore[method-assign]

    acquired = await asyncio.to_thread(_wait, lambda: keeper.lease is not None)
    first = keeper.lease
    renewed = await asyncio.to_thread(
        _wait,
        lambda: (
            (lease := keeper.lease) is not None
            and first is not None
            and lease.generation == first.generation
            and lease.expires_at > first.expires_at
        ),
    )
    tailed = await asyncio.to_thread(_wait, lambda: len(ticks) >= 3)
    report = {
        "boots": len(boots),
        "acquired": acquired,
        "renewed": renewed,
        "tailed": tailed,
        "may_manage": may_manage(),
        "threads_while_started": _threads(COORDINATION_THREADS),
    }

    await hangar.stop()
    await hangar.stop()  # a second stop does nothing
    return {
        **report,
        "lease_after_stop": None if keeper.lease is None else keeper.lease.holder,
        "threads_after_stop": _threads(COORDINATION_THREADS),
    }


def _coordination(out: Path) -> dict[str, Any]:
    from mcp_hangar.infrastructure.persistence.backends.sqlite import SqliteBackend

    SqliteBackend.shared_across_instances = True  # type: ignore[misc]
    config_path = out.parent / "config.yaml"
    # JSON is YAML.
    config_path.write_text(
        json.dumps(
            {
                "mcp_servers": {},
                "persistence": {"backend": "sqlite", "sqlite": {"data_dir": str(out.parent / "data")}},
                "coordination": {"lease_ttl_s": 5, "renew_deadline_s": 3, "renew_interval_s": 0.2},
            }
        )
    )
    boots = _count_boots()
    before = set(threading.enumerate())
    report = asyncio.run(_coordination_run(config_path, boots))
    return {**report, "left_running": _left_running(before)}


def _front_door(out: Path) -> dict[str, Any]:
    from mcp_hangar.facade import SyncHangar
    from mcp_hangar.server import catalogue_readiness

    config_path = out.parent / "config.yaml"
    config_path.write_text(
        json.dumps(
            {
                "mcp_servers": {
                    WARM: {"mode": "subprocess", "command": [sys.executable, str(MOCK_PROVIDER)]},
                    DOWN: {
                        "mode": "subprocess",
                        "command": [sys.executable, "-c", "raise SystemExit(1)"],
                        "max_consecutive_failures": 1000,
                    },
                },
                "tool_access": {
                    "mode": "front_door",
                    "required_catalogue": {"servers": [WARM, DOWN], "retry_for_s": 120},
                },
            }
        )
    )
    boots = _count_boots()
    before = set(threading.enumerate())
    hangar = SyncHangar.from_config(config_path)
    starters = [threading.Thread(target=hangar.start, name=f"starter-{i}") for i in range(2)]
    for starter in starters:
        starter.start()
    for starter in starters:
        starter.join(DEADLINE_S)

    context = hangar._hangar._context
    assert context is not None
    repository = context.runtime.repository
    # Nothing here calls `warm`: only the warm-up starts it.
    warmed = _wait(lambda: repository.get(WARM).state.value == "ready")
    detail: dict[str, Any] = {}

    def retrying() -> bool:
        detail.update(catalogue_readiness.catalogue_detail(repository) or {})
        return detail.get("missing") == [DOWN] and catalogue_readiness._gate._retry == "running"

    retry_running = _wait(retrying)
    report = {
        "boots": len(boots),
        "warmed": warmed,
        "retry_running": retry_running,
        "detail": detail,
        "warm_up_threads_while_started": _threads(frozenset({WARM_UP_THREAD})),
    }

    hangar.stop()
    hangar.stop()  # a second stop does nothing
    return {
        **report,
        # The retry's own record of how it ended.
        "retry_after_stop": catalogue_readiness._gate._retry,
        "warm_up_threads_after_stop": _threads(frozenset({WARM_UP_THREAD})),
        "left_running": _left_running(before),
    }


MODES = {"coordination": _coordination, "front_door": _front_door}


def main(mode: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data
    report = MODES[mode](out)
    out.write_text(json.dumps(report, default=str))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
