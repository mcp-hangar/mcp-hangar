"""Tier 0 live smoke: the shipped server starts and serves its operational surface.

This is the seed test that proves the live harness works end to end (real CLI
subprocess + real HTTP). Per-feature T0 verification (driving `hangar_call`,
withdrawal, digest pins, etc. over the MCP protocol) builds on this fixture --
see tests/live/README.md and docs/internal/LIVE_VERIFICATION.md.
"""

import httpx
import pytest

pytestmark = [pytest.mark.live, pytest.mark.t0]


def test_health_endpoint_responds(live_http_hangar):
    """Claim: `mcp-hangar serve --http` exposes a working liveness endpoint."""
    resp = httpx.get(f"{live_http_hangar}/health/live", timeout=5.0)
    assert resp.status_code == 200


def test_metrics_endpoint_exposes_prometheus(live_http_hangar):
    """Claim: the Prometheus /metrics endpoint serves mcp_hangar_* series."""
    resp = httpx.get(f"{live_http_hangar}/metrics", timeout=5.0)
    assert resp.status_code == 200
    assert "mcp_hangar_" in resp.text
