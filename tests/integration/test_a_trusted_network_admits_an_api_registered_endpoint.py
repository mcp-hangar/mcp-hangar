"""`ssrf.trusted_networks` lets the API register a private endpoint, and a reload takes it back (#1752).

``_trusted_network_harness.py`` runs in a fresh interpreter: the real
``bootstrap()``, ``POST /api/mcp_servers`` -- the route the Kubernetes operator
registers an in-cluster Service with (mcp-hangar-operator#164) -- and
``hangar_call`` to a real HTTP upstream on this host's private address.

Before #1752 the route registered with HUMAN provenance and nothing could relax
it, so every private endpoint answered 400 ``ssrf_blocked``: an operator could
not put a server it runs in the cluster into the pool.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_trusted_network_harness.py")


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("trusted-network") / "run.json"
    result = subprocess.run([sys.executable, str(HARNESS), str(out)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    report = json.loads(out.read_text())
    if "skipped" in report:
        pytest.skip(report["skipped"])
    return report


def test_without_trust_the_private_endpoint_is_refused(run):
    assert run["untrusted"] == {"status": 400, "body": {"error": "ssrf_blocked"}}


def test_a_trusted_network_admits_the_registration_and_its_calls(run):
    assert run["trusted"]["status"] == 201, run["trusted"]
    assert run["trusted_call"] == {"success": True, "error": None}
    assert run["upstream_called"] == ["add"]


def test_a_reload_without_the_network_refuses_the_next_connection(run):
    """Revocation reaches a server already registered, not only the next one."""
    assert run["revoked_call"]["success"] is False
    assert "SSRF" in run["revoked_call"]["error"], run["revoked_call"]
    assert run["upstream_called_after_revoke"] == ["add"], "the refused call must not reach the upstream"
    assert run["revoked_register"] == {"status": 400, "body": {"error": "ssrf_blocked"}}
