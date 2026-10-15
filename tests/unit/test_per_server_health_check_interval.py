"""A server's own `health_check_interval_s` decides how often it is checked (#1686).

The value was stored and reported, but the worker scheduled every server on its
global 60 s: the sweep only ran that often, and the next check was always `now +
interval_s` of the worker. A server declaring 5 s was checked every 60 s.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.gc import BackgroundWorker

MOCK = Path(__file__).resolve().parents[1] / "mock_provider.py"


def _server(name: str, interval: int | None) -> McpServer:
    kwargs = {} if interval is None else {"health_check_interval_s": interval}
    return McpServer(mcp_server_id=name, mode="subprocess", command=[sys.executable, str(MOCK)], **kwargs)


@pytest.fixture
def servers():
    made = {"fast": _server("fast", 5), "default": _server("default", None)}
    for s in made.values():
        s.ensure_ready()
    yield made
    for s in made.values():
        s.shutdown()


def _worker(servers, interval_s: int = 60) -> BackgroundWorker:
    return BackgroundWorker(servers, interval_s=interval_s, task="health_check", event_bus=MagicMock())


def test_the_server_reports_the_interval_it_declared(servers):
    assert servers["fast"].declared_health_check_interval_s == 5
    assert servers["default"].declared_health_check_interval_s is None


def test_the_next_check_is_scheduled_on_the_servers_own_interval(servers):
    worker = _worker(servers)
    now = time.time()

    worker._check_health("fast", servers["fast"], now)
    worker._check_health("default", servers["default"], now)

    assert worker._next_check_at["fast"] == pytest.approx(now + 5, abs=0.01)
    assert worker._next_check_at["default"] == pytest.approx(now + 60, abs=0.01)


def test_the_sweep_wakes_as_often_as_the_most_frequent_server(servers):
    assert _worker(servers)._tick_s() == 5.0


def test_with_every_server_at_the_default_the_sweep_is_unchanged():
    only_default = {"d": _server("d", None)}

    assert _worker(only_default)._tick_s() == 60.0


def test_the_gc_worker_keeps_its_own_interval(servers):
    gc = BackgroundWorker(servers, interval_s=30, task="gc", event_bus=MagicMock())

    assert gc._tick_s() == 30.0


def test_a_server_that_does_not_declare_an_interval_uses_the_workers():
    plain = MagicMock(spec=["state", "health", "health_check", "collect_events"])
    worker = BackgroundWorker({"p": plain}, interval_s=45, task="health_check", event_bus=MagicMock())

    assert worker._tick_s() == 45.0


def test_a_server_at_5_seconds_is_really_checked_about_every_5_seconds(servers):
    """The worker thread, in real time: two checks in about eleven seconds, not none."""
    fast = servers["fast"]
    calls: list[float] = []
    original = fast.health_check

    def counted() -> bool:
        calls.append(time.time())
        return original()

    fast.health_check = counted  # type: ignore[method-assign]
    worker = _worker({"fast": fast})
    worker.start()
    try:
        time.sleep(11.5)
    finally:
        worker.stop()
        worker.join(5)

    assert len(calls) >= 2, f"checked {len(calls)} times in 11.5 s"
    assert 4.0 <= calls[1] - calls[0] <= 7.0


def test_an_undeclared_interval_follows_the_workers_pace(servers):
    worker = _worker(servers, interval_s=7)
    now = time.time()

    worker._check_health("default", servers["default"], now)

    assert worker._next_check_at["default"] == pytest.approx(now + 7, abs=0.01)


def test_a_declared_interval_longer_than_the_workers_is_honoured():
    slow = _server("slow", 300)
    slow.ensure_ready()
    try:
        worker = _worker({"slow": slow}, interval_s=60)
        now = time.time()

        worker._check_health("slow", slow, now)

        assert worker._next_check_at["slow"] == pytest.approx(now + 300, abs=0.01)
    finally:
        slow.shutdown()
