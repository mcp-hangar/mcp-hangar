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


def test_rest_api_is_reachable_when_auth_is_disabled(live_http_hangar):
    """Claim: with auth off, the REST API answers instead of 401-ing (#600).

    The fixture runs the shipped CLI with no `auth` block. The API router
    deliberately mounts no authentication in that mode, so a guard that still
    demands a principal locks the API out with no credential able to open it --
    and takes the enforcement plane with it, since the operator delivers compiled
    L7 egress policy over exactly this surface.
    """
    resp = httpx.get(f"{live_http_hangar}/api/mcp_servers", follow_redirects=True, timeout=5.0)

    assert resp.status_code == 200, resp.text


def test_stdio_stdout_carries_only_jsonrpc(tmp_path):
    """Claim: on stdio, stdout carries JSON-RPC and nothing else (#563).

    Black-box against the shipped CLI, because that is the only place the bug
    lived: a log emitted while modules were still importing — before
    `setup_logging()` redirects to stderr — landed on stdout, and structlog's
    default factory writes there. One such line is enough for a strict client to
    fail parsing and drop the session, which is the Claude Desktop / Cursor path.
    """
    import json
    import shutil
    import subprocess
    import time

    binary = shutil.which("mcp-hangar")
    if binary is None:
        pytest.skip("`mcp-hangar` not on PATH (run under `uv run`)")

    config = tmp_path / "stdio.yaml"
    config.write_text("logging:\n  level: DEBUG\nmcp_servers: {}\n")

    proc = subprocess.Popen(
        [binary, "serve", "--config", str(config)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "stdio-probe", "version": "0"},
            },
        }
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        time.sleep(5)
    finally:
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=15)

    lines = [line for line in stdout.splitlines() if line.strip()]
    polluted = []
    for line in lines:
        try:
            json.loads(line)
        except ValueError:
            polluted.append(line)

    assert not polluted, f"non-JSON-RPC lines on stdout: {polluted[:3]}"
    assert lines, "the server answered nothing on stdout"
    # DEBUG level on purpose: the logs must still be produced, just elsewhere.
    assert stderr.strip(), "logging vanished entirely instead of moving to stderr"
