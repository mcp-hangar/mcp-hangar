"""The default ``/metrics`` body stays byte-identical (#1290, #1302).

Exemplars need OpenMetrics, served only when a scrape's ``Accept`` asks for
``application/openmetrics-text``. Everything else keeps getting text format
0.0.4 from the hand-rolled registry, and this snapshot pins those bytes for a
fixed set of samples: every family type, int and float values, ``+Inf``,
escaped label values, empty families and registration order. Any byte
difference fails, including the quirks of today's renderer that an exposition
library would normalise: an int gauge renders ``1``, not ``1.0``; int buckets
render ``le="100"``; families are separated by a blank line; there are no
``_created`` series. ``gc.py`` parses this text, and scrapers that do not ask
for OpenMetrics read it. A deliberate format change updates this file in the
same PR.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from mcp_hangar import metrics
from mcp_hangar.metrics import CollectorRegistry, Counter, Gauge, Histogram, Info, ScrapedGauge, Summary
from mcp_hangar.server.lifecycle import metrics_endpoint

EXPECTED = "\n".join(
    [
        "# HELP demo_zeta_events_total Registered first, rendered first",
        "# TYPE demo_zeta_events_total counter",
        "demo_zeta_events_total 1.0",
        "",
        "# HELP demo_calls_total Calls by tool and status",
        "# TYPE demo_calls_total counter",
        'demo_calls_total{status="success",tool="add"} 3.0',
        'demo_calls_total{status="error",tool="q\\"uote\\\\back\\nline"} 1.0',
        'demo_calls_total{status="success",tool="sub"} 0.5',
        "",
        "# HELP demo_idle_total A counter nothing has incremented",
        "# TYPE demo_idle_total counter",
        "",
        "# HELP demo_up Gauge set from ints and floats",
        "# TYPE demo_up gauge",
        'demo_up{server="b"} 1',
        'demo_up{server="a"} 0.25',
        'demo_up{server="c"} 10000000000000000000000',
        'demo_up{server="d"} 1e-07',
        'demo_up{server="e"} -3',
        'demo_up{server="f"} 9007199254740992',
        "",
        "# HELP demo_plain An unlabeled gauge",
        "# TYPE demo_plain gauge",
        "demo_plain 0",
        "",
        "# HELP demo_held A gauge read at scrape time",
        "# TYPE demo_held gauge",
        'demo_held{server="s",tool="t"} 1.0',
        'demo_held{server="s2",tool="t2"} 1.0',
        "",
        "# HELP demo_latency_seconds Float buckets",
        "# TYPE demo_latency_seconds histogram",
        'demo_latency_seconds_bucket{le="0.005",server="s1"} 1',
        'demo_latency_seconds_bucket{le="0.1",server="s1"} 1',
        'demo_latency_seconds_bucket{le="1.0",server="s1"} 2',
        'demo_latency_seconds_bucket{le="+Inf",server="s1"} 3',
        'demo_latency_seconds_bucket{le="0.005",server="s0"} 0',
        'demo_latency_seconds_bucket{le="0.1",server="s0"} 1',
        'demo_latency_seconds_bucket{le="1.0",server="s0"} 1',
        'demo_latency_seconds_bucket{le="+Inf",server="s0"} 1',
        'demo_latency_seconds_sum{server="s1"} 7.503',
        'demo_latency_seconds_sum{server="s0"} 0.1',
        'demo_latency_seconds_count{server="s1"} 3',
        'demo_latency_seconds_count{server="s0"} 1',
        "",
        "# HELP demo_size_bytes Int buckets",
        "# TYPE demo_size_bytes histogram",
        'demo_size_bytes_bucket{le="100"} 1',
        'demo_size_bytes_bucket{le="1000"} 1',
        'demo_size_bytes_bucket{le="+Inf"} 2',
        "demo_size_bytes_sum 5050.0",
        "demo_size_bytes_count 2",
        "",
        "# HELP demo_wait_seconds A summary",
        "# TYPE demo_wait_seconds summary",
        'demo_wait_seconds_sum{server="s"} 1.0',
        'demo_wait_seconds_count{server="s"} 2',
        "",
        "# HELP demo_build_info Build metadata",
        "# TYPE demo_build_info gauge",
        'demo_build_info{python="3.11",version="1.2.3"} 1',
        "",
        "# HELP demo_unset_info An info never set",
        "# TYPE demo_unset_info gauge",
        "",
    ]
)


def _registry() -> CollectorRegistry:
    """Fixed samples on a private registry; the global one holds live values."""
    registry = CollectorRegistry()
    zeta = Counter("demo_zeta_events", "Registered first, rendered first")
    calls = Counter("demo_calls", "Calls by tool and status", ["tool", "status"])
    idle = Counter("demo_idle", "A counter nothing has incremented", ["tool"])
    up = Gauge("demo_up", "Gauge set from ints and floats", ["server"])
    plain = Gauge("demo_plain", "An unlabeled gauge")
    held = ScrapedGauge("demo_held", "A gauge read at scrape time", ["server", "tool"])
    latency = Histogram("demo_latency_seconds", "Float buckets", ["server"], buckets=(0.005, 0.1, 1.0))
    size = Histogram("demo_size_bytes", "Int buckets", buckets=(100, 1000))
    wait = Summary("demo_wait_seconds", "A summary", ["server"])
    build = Info("demo_build", "Build metadata")
    unset = Info("demo_unset", "An info never set")
    for collector in (zeta, calls, idle, up, plain, held, latency, size, wait, build, unset):
        registry.register(collector)

    zeta.inc()
    calls.inc(tool="add", status="success")
    calls.inc(2, tool="add", status="success")
    calls.inc(tool='q"uote\\back\nline', status="error")
    calls.labels(tool="sub", status="success").inc(0.5)
    up.set(1, server="b")
    up.set(0.25, server="a")
    up.set(10**22, server="c")
    up.set(1e-07, server="d")
    up.dec(3, server="e")
    up.set(2**53, server="f")
    plain.set(0)
    held.read_from(lambda: [{"tool": "t", "server": "s"}, {"server": "s2", "tool": "t2"}])
    latency.observe(0.003, server="s1")
    latency.observe(0.5, server="s1")
    latency.observe(7, server="s1")
    latency.observe(0.1, server="s0")  # on a bound: counted in le="0.1"
    size.observe(50)
    size.observe(5000)
    wait.observe(0.25, server="s")
    wait.observe(0.75, server="s")
    build.info(version="1.2.3", python="3.11")
    return registry


def test_the_registry_renders_the_snapshot_byte_for_byte() -> None:
    assert _registry().collect() == EXPECTED


def _scrape(accept: str | None) -> Request:
    headers = [] if accept is None else [(b"accept", accept.encode())]
    return Request({"type": "http", "method": "GET", "path": "/metrics", "headers": headers})


@pytest.mark.parametrize(
    "accept",
    [None, "", "*/*", "text/plain", "text/plain; version=0.0.4", "application/json"],
)
def test_a_scrape_that_does_not_ask_for_openmetrics_gets_the_snapshot(monkeypatch, accept) -> None:
    monkeypatch.setattr(metrics, "REGISTRY", _registry())

    response = metrics_endpoint(_scrape(accept))

    assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert response.body == EXPECTED.encode()
