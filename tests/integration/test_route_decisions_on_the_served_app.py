"""A group call's route reaches its spans on the app ``serve --http`` serves, on both entry paths (#1286).

The unit tests drive `BatchExecutor` with a stand-in context and bind the
tenant themselves. Here each topology runs ``_route_decisions_harness.py`` in a
fresh interpreter: the real ``bootstrap()`` over a config file, API-key auth,
and the served app. The pin is keyed on the tenant the key authenticated, so a
tenant that never reached the executor would read ``load_balanced`` where its
pin says ``pinned``.

Two entry paths reach the same selection: ``hangar_call`` naming the group, and
the front door's flat tool of a group member, which the front door routes
through that member's group (`flat_tool_projection._member_to_group`). Both must
say the same thing: ``mcp.server.id`` is the group on every span the executor
opened, and the member is ``hangar.route.backend``.

Naming: neutral placeholders only (pool, route-a, route-b, tenant-*).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.otel_sdk

HARNESS = Path(__file__).with_name("_route_decisions_harness.py")
GROUP, MEMBER_A, MEMBER_B = "pool", "route-a", "route-b"
PINNED_TENANT, OTHER_TENANT = "tenant-a", "tenant-b"


@pytest.fixture(scope="module", params=["egress", "front_door"])
def run(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp(f"route_{request.param}") / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), request.param, str(out)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{request.param}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    report = json.loads(out.read_text())
    # The harness must have imported this checkout, not an installed copy.
    assert report["hangar"].startswith(str(Path(__file__).resolve().parents[2])), report["hangar"]
    return dict(report["report"])


def test_the_pinned_tenant_reads_pinned_and_reaches_its_member(run):
    call = run[PINNED_TENANT]

    assert call["call_spans"] == 1, call
    assert (call["reason"], call["backend"]) == ("pinned", MEMBER_B), call
    assert call["reached"] == [MEMBER_B], call


def test_another_tenant_is_load_balanced(run):
    call = run[OTHER_TENANT]

    assert (call["reason"], call["backend"]) == ("load_balanced", MEMBER_A), call
    assert call["reached"] == [MEMBER_A], call


@pytest.mark.parametrize(("tenant", "member"), [(PINNED_TENANT, MEMBER_B), (OTHER_TENANT, MEMBER_A)])
def test_mcp_server_id_is_the_group_on_every_executor_span_and_the_member_is_the_backend(run, tenant, member):
    spans = run[tenant]["spans"]
    names = {name for name, _, _ in spans}
    assert "command.send.InvokeToolCommand" in names, spans

    assert {server_id for _, server_id, _ in spans} == {GROUP}, spans
    for name, _, backend in spans:
        if name in ("mcp_server.cold_start", "command.send.InvokeToolCommand", "batch.call.whoami"):
            assert backend == member, (name, spans)
