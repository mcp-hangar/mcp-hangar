"""Tier 0 live: the last-healthy time is on the real /metrics, and a stop keeps it (#1359).

Black-box against the shipped `serve --http`: a start over the REST API, a
stop, and the Prometheus exposition scraped over HTTP. The in-process test
(tests/integration/test_a_given_up_server_reads_dead_on_the_served_path.py)
covers the give-up; this pins that the series reaches a real scrape, which is
where #1059's five metrics were missing for seven versions.
"""

import time

import httpx
import pytest

pytestmark = [pytest.mark.live, pytest.mark.t0]

LAST_HEALTHY = "mcp_hangar_mcp_server_last_healthy_timestamp_seconds"


def _sample(base_url: str, name: str, server: str = "math") -> float | None:
    resp = httpx.get(f"{base_url}/metrics", timeout=5.0)
    assert resp.status_code == 200
    prefix = f'{name}{{mcp_server="{server}"}} '
    values = [float(line.split()[-1]) for line in resp.text.splitlines() if line.startswith(prefix)]
    return values[0] if values else None


def test_a_started_server_has_a_last_healthy_time_that_a_stop_keeps(live_http_hangar):
    start = httpx.post(f"{live_http_hangar}/api/mcp_servers/math/start", timeout=30.0)
    assert start.status_code == 200, start.text

    healthy = _sample(live_http_hangar, LAST_HEALTHY)
    assert healthy is not None, f"{LAST_HEALTHY} is not on /metrics after a start"
    assert healthy <= time.time()

    stop = httpx.post(f"{live_http_hangar}/api/mcp_servers/math/stop", timeout=10.0)
    assert stop.status_code == 200, stop.text

    assert _sample(live_http_hangar, "mcp_hangar_mcp_server_state") == 0.0
    kept = _sample(live_http_hangar, LAST_HEALTHY)
    assert kept is not None and kept >= healthy, "a stop cleared or moved back the last-healthy time"
