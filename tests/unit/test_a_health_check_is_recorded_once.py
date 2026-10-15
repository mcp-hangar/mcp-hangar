"""One health check moves the health-check metrics once, with its measured duration (#1707).

`gc.py` observed every check itself and the metrics event handler observed the
`HealthCheckPassed`/`HealthCheckFailed` the same check recorded, so
`mcp_hangar_health_checks_total` rose by 2 per check. The event path also wrote a
0 s duration for each failure, pulling the percentiles down. A check is now
recorded on the event path only, and a failure carries the time the probe took.

The check runs on a real `McpServer` driven by the real background worker; only
the event bus is replaced by a function that hands each published event to the
real `MetricsEventHandler`.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_hangar import metrics
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.gc import BackgroundWorker
from mcp_hangar.infrastructure.observability.metrics_event_handler import MetricsEventHandler

MOCK = Path(__file__).resolve().parents[1] / "mock_provider.py"
NAME = "once"


def _counts() -> dict[str, float]:
    return {
        s.labels["result"]: s.value for s in metrics.HEALTH_CHECK_TOTAL.collect() if s.labels.get("mcp_server") == NAME
    }


def _durations() -> tuple[int, float]:
    key = (NAME,)
    return metrics.HEALTH_CHECK_DURATION_SECONDS._counts.get(key, 0), metrics.HEALTH_CHECK_DURATION_SECONDS._sums.get(
        key, 0.0
    )


@pytest.fixture
def worker_and_server():
    server = McpServer(mcp_server_id=NAME, mode="subprocess", command=[sys.executable, str(MOCK)])
    server.ensure_ready()
    handler = MetricsEventHandler()
    bus = MagicMock()
    bus.publish_aggregate_events.side_effect = lambda _t, _id, events: [handler.handle(e) for e in events]
    worker = BackgroundWorker({NAME: server}, interval_s=60, task="health_check", event_bus=bus)
    yield worker, server
    server.shutdown()


def test_a_passing_check_counts_once_with_its_duration(worker_and_server):
    worker, _ = worker_and_server
    before, (n0, s0) = _counts().get("healthy", 0), _durations()

    worker._check_health(NAME, worker.mcp_servers[NAME], time.time())

    assert _counts().get("healthy", 0) - before == 1
    n1, s1 = _durations()
    assert n1 - n0 == 1
    assert s1 - s0 > 0


def test_a_failing_check_counts_once_with_the_time_it_took(worker_and_server):
    worker, server = worker_and_server

    def slow_failure(*_a, **_k):
        time.sleep(0.05)
        raise TimeoutError("no answer")

    server._client.call = slow_failure
    before, (n0, s0) = _counts().get("unhealthy", 0), _durations()

    worker._check_health(NAME, server, time.time())

    assert _counts().get("unhealthy", 0) - before == 1
    n1, s1 = _durations()
    assert n1 - n0 == 1
    assert s1 - s0 >= 0.05, "a failure was recorded as a 0 s check"
