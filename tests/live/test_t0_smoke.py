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


def test_readiness_is_green_with_a_configured_but_cold_backend(live_http_hangar):
    """Claim: an idle gateway is READY even though no backend is warm (#599).

    The fixture's backend is configured and never invoked, so it sits `cold` --
    the same state every backend returns to after `idle_ttl_s`. Readiness used to
    require a warm one, which in Kubernetes removed the pod from its Service and
    left nothing able to send the call that would warm a backend.

    Black-box on purpose: this is the probe kubelet actually runs.
    """
    resp = httpx.get(f"{live_http_hangar}/health/ready", timeout=5.0)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["ready_mcp_servers"] == 0, "the fixture backend should still be cold"
    assert body["total_mcp_servers"] >= 1, "a backend must be configured for this to mean anything"
