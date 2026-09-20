"""A front-door member of several groups is governed by each of them, on the app ``serve --http`` serves.

The front door kept one group per member. A server in groups ``pool-a`` and
``pool-b`` was governed by only one of them there, whichever the file declared
last, while ``hangar_call`` naming that server was governed by both. The two
paths gave different decisions for the same config.

Each run is ``_front_door_member_groups_harness.py`` in a fresh interpreter:
the real ``bootstrap()`` over a config file, the served app behind API-key
auth, and two in-process HTTP upstreams. Nothing in the policy, withdrawal or
pin path is mocked or registered by hand.

``shared-member`` is in ``pool-a``, which denies ``a_denied``, and in
``pool-b``, which denies ``b_denied``, withdraws ``b_withdrawn`` for every
tenant and ``b_withdrawn_for_a`` for ``tenant-a``, and pins ``b_pinned`` to a
digest nothing matches. ``single-member`` is the one member of ``pool-c``,
which denies ``c_denied`` and withdraws ``c_withdrawn``. Each config is run
with ``pool-a`` declared first and with ``pool-b`` declared first, on the front
door and on egress.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_front_door_member_groups_harness.py")
TOPOLOGIES = ("front_door", "egress")
ORDERS = ("ab", "ba")
TENANTS = ("tenant-a", "tenant-b")

#: The shared table: each tool's decision for each tenant, whichever path calls
#: it and whichever order the file declares the two groups in.
DECISIONS: dict[str, dict[str, str]] = {
    "shared_ok": {"tenant-a": "ok", "tenant-b": "ok"},
    "a_denied": {"tenant-a": "refused", "tenant-b": "refused"},
    "b_denied": {"tenant-a": "refused", "tenant-b": "refused"},
    "b_withdrawn": {"tenant-a": "refused", "tenant-b": "refused"},
    "b_withdrawn_for_a": {"tenant-a": "refused", "tenant-b": "ok"},
    "b_pinned": {"tenant-a": "refused", "tenant-b": "refused"},
    "single_ok": {"tenant-a": "ok", "tenant-b": "ok"},
    "c_denied": {"tenant-a": "refused", "tenant-b": "refused"},
    "c_withdrawn": {"tenant-a": "refused", "tenant-b": "refused"},
}
#: A pin is checked when the call is made, so a pinned tool is listed.
LISTED = {
    "tenant-a": ["b_pinned", "shared_ok", "single_ok"],
    "tenant-b": ["b_pinned", "b_withdrawn_for_a", "shared_ok", "single_ok"],
}
SINGLE_TOOLS = ("single_ok", "c_denied", "c_withdrawn")


def _run(topology: str, order: str, tmp: Path) -> dict[str, Any]:
    out = tmp / f"{topology}-{order}" / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), topology, order, str(out)],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{topology}/{order}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return json.loads(out.read_text())


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str], dict[str, Any]]:
    # Concurrently, under the 60s pytest-timeout the integration job applies.
    tmp = tmp_path_factory.mktemp("member-groups")
    combinations = list(itertools.product(TOPOLOGIES, ORDERS))
    with ThreadPoolExecutor(max_workers=len(combinations)) as pool:
        pending = {run: pool.submit(_run, *run, tmp) for run in combinations}
        return {run: future.result() for run, future in pending.items()}


def _decisions(runs: dict[tuple[str, str], dict[str, Any]], topology: str, order: str) -> dict[str, dict[str, str]]:
    report = runs[(topology, order)]["report"]
    return {tool: {tenant: report[tenant]["calls"][tool]["decision"] for tenant in TENANTS} for tool in DECISIONS}


@pytest.mark.parametrize("order", ORDERS)
class TestTheFrontDoor:
    def test_a_member_of_two_groups_is_refused_what_either_group_refuses(self, runs, order: str) -> None:
        assert _decisions(runs, "front_door", order) == DECISIONS, runs[("front_door", order)]["hangar"]

    @pytest.mark.parametrize("tenant", TENANTS)
    def test_the_listing_leaves_out_what_either_group_denies_or_withdraws(self, runs, order: str, tenant: str) -> None:
        assert runs[("front_door", order)]["report"][tenant]["listed"] == LISTED[tenant]

    @pytest.mark.parametrize("tenant", TENANTS)
    def test_a_refused_call_does_not_reach_the_upstream(self, runs, order: str, tenant: str) -> None:
        calls = runs[("front_door", order)]["report"][tenant]["calls"]

        assert {tool: call["reached_upstream"] for tool, call in calls.items()} == {
            tool: DECISIONS[tool][tenant] == "ok" for tool in DECISIONS
        }

    @pytest.mark.parametrize("tenant", TENANTS)
    def test_a_member_of_one_group_is_governed_as_before(self, runs, order: str, tenant: str) -> None:
        calls = runs[("front_door", order)]["report"][tenant]["calls"]

        assert {tool: calls[tool]["decision"] for tool in SINGLE_TOOLS} == {
            "single_ok": "ok",
            "c_denied": "refused",
            "c_withdrawn": "refused",
        }


@pytest.mark.parametrize("order", ORDERS)
def test_hangar_call_gives_the_same_decisions(runs, order: str) -> None:
    assert _decisions(runs, "egress", order) == DECISIONS


@pytest.mark.parametrize("order", ORDERS)
def test_the_front_door_and_hangar_call_agree(runs, order: str) -> None:
    assert _decisions(runs, "front_door", order) == _decisions(runs, "egress", order)
