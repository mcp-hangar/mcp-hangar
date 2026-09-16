"""A reload keeps an L7 egress policy set over the API, on the app ``serve --http`` serves (#1498).

An L7 egress policy is set at runtime -- over ``POST
/api/mcp_servers/{id}/l7_policy``, or by the fleet projection -- and no
configuration file declares one. A reload that rebuilt a server, because a
setting it is built from changed, built the new object from the file alone and
dropped the policy: the next call ran ungoverned, and the reload reported
success.

``_reload_l7_policy_harness.py`` runs in a fresh interpreter: the real
``bootstrap()`` from a file, real stdio subprocess servers, the policy and the
reload over REST, and the calls through ``hangar_call`` over streamable HTTP.
The unit-level versions, through the same ``ReloadConfigurationHandler``, are in
``tests/unit/test_a_reload_applies_the_whole_configuration.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

# One gateway, booted and reloaded once, with two real upstreams.
pytestmark = pytest.mark.timeout(120)

HARNESS = Path(__file__).with_name("_reload_l7_policy_harness.py")
DENIED = "EgressPolicyDeniedError"


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    workdir = tmp_path_factory.mktemp("reload-l7")
    out = workdir / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(workdir), str(out)],
        capture_output=True,
        text=True,
        timeout=110,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-6000:]}"
    return dict(json.loads(out.read_text()))


def test_the_policy_was_in_force_before_the_reload(run: dict[str, Any]) -> None:
    # Otherwise a refusal after the reload would say nothing.
    assert run["boot"]["calls"] == {"keep": "served", "edit": "served"}
    assert run["boot"]["set"] == {"keep": 200, "edit": 200}
    assert run["under_policy"]["calls"] == {"keep": DENIED, "edit": DENIED}


def test_the_reload_rebuilds_only_the_edited_server(run: dict[str, Any]) -> None:
    edited = run["edited"]

    assert edited["status"] == 200
    assert edited["diff"]["mcp_servers_updated"] == ["edit"]
    assert edited["diff"]["mcp_servers_unchanged"] == ["keep"]
    assert edited["same_object"] == {"keep": True, "edit": False}
    # The mock names its `add` tool after this variable: a new process, new env.
    assert edited["edit_description"] == "after"


@pytest.mark.parametrize("mcp_server_id", ["keep", "edit"])
def test_the_policy_survives_the_reload_and_still_refuses(run: dict[str, Any], mcp_server_id: str) -> None:
    edited = run["edited"]

    assert edited["policy"][mcp_server_id] != 404, "the gateway holds no policy for it any more"
    assert edited["policy"][mcp_server_id]["tools"]["deny"] == ["add"]
    assert edited["calls"][mcp_server_id] == DENIED
