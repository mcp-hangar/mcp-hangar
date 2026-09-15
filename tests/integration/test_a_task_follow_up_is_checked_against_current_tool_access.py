"""A task's follow-ups get the answer a new call of its tool gets, after a reload takes the tool away (#1473).

A task is its tool's call carried on. Its follow-ups used to be checked for
ownership and a suspended session only, so once a reload withdrew a tool, or
stopped its policy allowing the tool for a tenant, a new call of the tool was
refused and its task was still driven through ``tasks/update``.

Each topology runs ``_task_follow_up_access_harness.py`` in a fresh
interpreter: the real ``bootstrap()`` over a config file, API-key auth with a
key for each of two tenants, the served app, and in-process upstreams whose
every ``tools/call`` answers with a task. Each tenant creates a task with every
tool, then the file is reloaded, taking these away:

- ``job_withdrawn`` is withdrawn on the group for every tenant;
- ``job_withdrawn_a`` is withdrawn on the group for ``tenant-a``;
- ``job_denied`` is denied by the group's policy;
- ``solo_denied_a`` is denied by the ungrouped server's policy for ``tenant-a``.

Then each owner polls, updates and cancels each task, and calls its tool again.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_task_follow_up_access_harness.py")
TOPOLOGIES = ("front_door", "egress")
TENANTS = ("tenant-a", "tenant-b")
TOOLS = ("job", "job_withdrawn", "job_withdrawn_a", "job_denied", "solo_job", "solo_denied_a")

WITHDRAWN = "ToolWithdrawnError"
DENIED = "ToolAccessDeniedError"
#: What the reload took away from each tenant, and the refusal a call of it gets.
TAKEN_AWAY = {
    "tenant-a": {
        "job_withdrawn": WITHDRAWN,
        "job_withdrawn_a": WITHDRAWN,
        "job_denied": DENIED,
        "solo_denied_a": DENIED,
    },
    "tenant-b": {"job_withdrawn": WITHDRAWN, "job_denied": DENIED},
}
MESSAGES = {WITHDRAWN: "Tool '{tool}' is withdrawn for this tenant", DENIED: "Tool not available for this mcp_server"}


def _run(topology: str, tmp: Path) -> dict[str, Any]:
    out = tmp / topology / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), topology, str(out)],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{topology}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return json.loads(out.read_text())


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    # Concurrently, under the 60s pytest-timeout the integration job applies.
    tmp = tmp_path_factory.mktemp("task-follow-ups")
    with ThreadPoolExecutor(max_workers=len(TOPOLOGIES)) as pool:
        pending = {topology: pool.submit(_run, topology, tmp) for topology in TOPOLOGIES}
        return {topology: future.result() for topology, future in pending.items()}


def _tasks(runs: dict[str, dict[str, Any]], topology: str, tenant: str) -> dict[str, dict[str, Any]]:
    return runs[topology]["tasks"][tenant]


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("topology", TOPOLOGIES)
class TestAfterTheReload:
    def test_every_tool_had_created_a_task(self, runs, topology: str, tenant: str) -> None:
        """Positive control: each follow-up below is of a task that exists."""
        assert runs[topology]["reload"]["success"] is True, runs[topology]["reload"]
        for tool, row in _tasks(runs, topology, tenant).items():
            assert row["created"] == "task", (tool, row, runs[topology]["hangar"])

    def test_an_update_is_refused_with_the_code_a_call_gets(self, runs, topology: str, tenant: str) -> None:
        for tool, error_type in TAKEN_AWAY[tenant].items():
            row = _tasks(runs, topology, tenant)[tool]

            assert row["update"] == {
                "outcome": "refused",
                "code": -32602,
                "message": MESSAGES[error_type].format(tool=tool),
                "error_type": error_type,
            }, tool
            assert row["update_sent"] is False, f"{tool}: the upstream was sent the refused update"

    def test_a_cancel_still_works(self, runs, topology: str, tenant: str) -> None:
        for tool, row in _tasks(runs, topology, tenant).items():
            assert row["cancel"] == {"outcome": "ok"}, tool
            assert row["cancel_sent"] is True, tool

    def test_a_status_is_still_served(self, runs, topology: str, tenant: str) -> None:
        for tool, row in _tasks(runs, topology, tenant).items():
            assert row["polled"] == "working", tool

    def test_a_tool_still_allowed_is_updated_as_before(self, runs, topology: str, tenant: str) -> None:
        for tool in set(TOOLS) - set(TAKEN_AWAY[tenant]):
            row = _tasks(runs, topology, tenant)[tool]

            assert row["update"] == {"outcome": "ok"}, tool
            assert row["update_sent"] is True, tool
            assert row["called_again"]["outcome"] == "task", tool


@pytest.mark.parametrize("tenant", TENANTS)
class TestANewCallAgrees:
    def test_hangar_call_is_refused_with_the_same_code(self, runs, tenant: str) -> None:
        for tool, error_type in TAKEN_AWAY[tenant].items():
            called = _tasks(runs, "egress", tenant)[tool]["called_again"]

            assert called == {"outcome": "refused", "detail": error_type}, tool

    def test_a_front_door_does_not_find_it(self, runs, tenant: str) -> None:
        """On a front door a tool a caller may not call is not listed, so its call is not found."""
        for tool in TAKEN_AWAY[tenant]:
            assert _tasks(runs, "front_door", tenant)[tool]["called_again"] == {"outcome": "error -32601"}, tool
