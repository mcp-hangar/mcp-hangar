"""A reload applies the whole configuration, on the app ``serve --http`` serves (#1424).

A reload reset the topology mode to ``egress`` and applied only
``mcp_servers``. On a ``front_door`` gateway, an API key issued without a tenant
went from an empty ``tools/list`` to the full list after any reload, and its
calls were served. ``interceptors``, ``ui_resources``, ``headers.param_validation``,
``resource_links`` and ``execution`` kept their boot values.

Each scenario runs ``_reload_served_harness.py`` in a fresh interpreter: the
real ``bootstrap()`` from a file, the app composed as ``serve --http`` composes
it, reloaded over REST, SIGHUP and the file watcher. The live tier runs the same
front-door check against the ``mcp-hangar`` binary (``tests/live``); this one is
what CI runs. Unit-level coverage of the handler, the policy swap and every
section is ``tests/unit/test_a_reload_applies_the_whole_configuration.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from mcp_hangar.server.tools.batch.concurrency import DEFAULT_GLOBAL_CONCURRENCY, DEFAULT_PROVIDER_CONCURRENCY
from mcp_hangar.tasks_wire import HEADER_MISMATCH

# Two gateways, each booted and reloaded eight or three times with a real
# upstream restarted each time: more than the job's default per-test budget.
pytestmark = pytest.mark.timeout(180)

HARNESS = Path(__file__).with_name("_reload_served_harness.py")
SCENARIOS = ("front_door", "egress")
METHOD_NOT_FOUND = -32601


def _run(scenario: str, tmp: Path) -> dict[str, Any]:
    workdir = tmp / scenario
    workdir.mkdir()
    out = workdir / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), scenario, str(workdir), str(out)],
        capture_output=True,
        text=True,
        timeout=170,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{scenario}: harness exited {result.returncode}:\n{result.stderr[-6000:]}"
    )
    return dict(json.loads(out.read_text()))


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("reload-served")
    with ThreadPoolExecutor(max_workers=len(SCENARIOS)) as pool:
        pending = {scenario: pool.submit(_run, scenario, tmp) for scenario in SCENARIOS}
        return {scenario: future.result() for scenario, future in pending.items()}


FRONT_DOOR_HOLDS = {
    "mode": "front_door",
    "no_tenant_tools": [],
    "no_tenant_call": METHOD_NOT_FOUND,
    "tenant_sees_echo": True,
    "tenant_call": "served",
}


class TestAFrontDoorStaysAFrontDoor:
    @pytest.mark.parametrize("phase", ["boot", "rest", "sighup", "watcher", "deleted"])
    def test_a_key_without_a_tenant_sees_nothing_and_calls_nothing(self, runs, phase: str) -> None:
        probe = {key: value for key, value in runs["front_door"]["phases"][phase].items() if key != "status"}

        assert probe == FRONT_DOOR_HOLDS

    def test_the_rest_reload_succeeded(self, runs) -> None:
        # So the phase after it is a reloaded gateway, not a refused reload.
        assert runs["front_door"]["phases"]["rest"]["status"] == 200


class TestAModeChangeIsRefused:
    def test_it_answers_409_and_says_a_restart_is_required(self, runs) -> None:
        refused = runs["front_door"]["refused"]

        assert refused["status"] == 409
        assert "Restart the gateway" in refused["message"]

    def test_nothing_changed(self, runs) -> None:
        refused = runs["front_door"]["refused"]

        assert {key: refused[key] for key in FRONT_DOOR_HOLDS} == FRONT_DOOR_HOLDS
        assert refused["servers_unchanged"] is True


class TestFrontDoorSections:
    """Booted, edited by one reload, deleted by the next; read off the served surface."""

    def test_the_fault_reached_the_listing_every_time(self, runs) -> None:
        # Otherwise `served` below would say nothing about param_validation.
        assert {phase: s["listing_failed"] for phase, s in runs["front_door"]["sections"].items()} == {
            "boot": True,
            "edited": True,
            "deleted": True,
        }

    def test_ui_resources(self, runs) -> None:
        sections = runs["front_door"]["sections"]

        assert [sections[phase]["ui_listed"] for phase in ("boot", "edited", "deleted")] == [False, True, False]

    def test_resource_links(self, runs) -> None:
        sections = runs["front_door"]["sections"]

        # The upstream hands out note://1..3 on every call; the tenant's newest
        # `max_per_tenant` are remembered, and listed in the links union.
        assert [sections[phase]["links"] for phase in ("boot", "edited", "deleted")] == [
            ["2", "3"],
            ["3"],
            ["1", "2", "3"],
        ]

    def test_interceptors_on_a_flat_call(self, runs) -> None:
        sections = runs["front_door"]["sections"]

        assert [sections[phase]["oversized_flat_call"] for phase in ("boot", "edited", "deleted")] == [
            "served",
            "error-result",
            "served",
        ]

    def test_headers_param_validation(self, runs) -> None:
        sections = runs["front_door"]["sections"]

        assert [sections[phase]["unvalidated_call"] for phase in ("boot", "edited", "deleted")] == [
            "served",
            HEADER_MISMATCH,
            "served",
        ]


class TestEgressSections:
    def test_the_reloads_succeeded(self, runs) -> None:
        assert [runs["egress"][phase]["status"] for phase in ("edited", "deleted")] == [200, 200]

    def test_interceptors(self, runs) -> None:
        egress = runs["egress"]

        assert [egress[phase]["small"] for phase in ("boot", "edited", "deleted")] == [[True, None]] * 3
        assert [egress[phase]["oversized"] for phase in ("boot", "edited", "deleted")] == [
            [True, None],
            [False, "ValidatorDenied"],
            [True, None],
        ]

    def test_execution(self, runs) -> None:
        egress = runs["egress"]

        assert egress["boot"]["execution"] == {"max_concurrency": 7, "default_mcp_server_concurrency": 3}
        assert egress["edited"]["execution"] == {"max_concurrency": 2, "default_mcp_server_concurrency": 1}
        assert egress["deleted"]["execution"] == {
            "max_concurrency": DEFAULT_GLOBAL_CONCURRENCY,
            "default_mcp_server_concurrency": DEFAULT_PROVIDER_CONCURRENCY,
        }
