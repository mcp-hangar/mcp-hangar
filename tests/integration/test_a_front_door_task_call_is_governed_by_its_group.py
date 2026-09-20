"""A front door's flat call to a group's task tool is governed as ``hangar_call`` is, on the served app.

The front door dispatches a flat ``tools/call`` through the tool's group
(#857). When the upstream answers with a task handle, the result goes back to
the caller through the relay seam ``hangar_call`` runs, which records who owns
the task (#1394). Every gate runs before that: the group's and the tenant's
tool access, their withdrawals and their approval lists. A call a gate refuses
never reaches the upstream, so there is no task for the seam to hand anyone.

Each topology runs ``_front_door_task_governance_harness.py`` in a fresh
interpreter: the real ``bootstrap()`` over a config dict, API-key auth with a
key for each of two tenants, and two in-process upstreams whose every
``tools/call`` answers with a task. On the front door each call is the flat
``tools/call`` of the tool's name. On egress it is ``hangar_call`` naming the
group or the ungrouped server, which is the path the two must agree with.

The group ``job-pool`` denies ``job_denied``, withdraws ``job_withdrawn`` for
every tenant and ``job_withdrawn_a`` for ``tenant-a``, and holds ``job_held``
for approval. ``job-solo`` is in no group. It holds ``solo_held_a`` for
approval for ``tenant-a`` alone. Both approval lists time out after one second,
and nobody answers.

The approval gate used to read only the named server's own list, and asked
without the caller's tenant. On a front door that lookup is answered with the
deny-all policy for a caller with no identity, which asks for no approval, so
no approval list held a flat call: ``job_held`` and ``solo_held_a`` each ran
and answered with a task.

A third server, ``job-flat``, answers in SEP-2663's flat task shape rather than
the nested one. Its task is governed and polled the same way. A caller that did
not declare the tasks extension is refused a task on both paths, and the task
it was refused is not recorded (#1405).

A fourth, ``job-spec``, is the upstream SEP-2663 describes: it creates a task
only for a caller that declared the extension. The front door dispatched its
flat call without the request context, so the caller's declaration was never
read and never forwarded, and this upstream never created a task there -- only
the three that create one unasked ever produced one on a front door. Each tenant
calls it twice, declaring and not, and the two answers differ (#1492).

A task refused to a caller that cannot poll it is one nobody will ever collect,
so its upstream is asked to cancel it. The upstreams record every
``tasks/cancel`` they are sent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_front_door_task_governance_harness.py")
TOPOLOGIES = ("front_door", "egress")
TENANTS = ("tenant-a", "tenant-b")

# As `_front_door_task_governance_harness.py` names them.
TOOLS = (
    "job",
    "job_denied",
    "job_withdrawn",
    "job_withdrawn_a",
    "job_held",
    "solo_job",
    "solo_held_a",
    "flat_job",
    "spec_job",
)
REFUSED = {
    "tenant-a": {"job_denied", "job_withdrawn", "job_withdrawn_a", "job_held", "solo_held_a"},
    "tenant-b": {"job_denied", "job_withdrawn", "job_held"},
}
#: Refused by an approval list rather than by access or withdrawal.
HELD = {"tenant-a": {"job_held", "solo_held_a"}, "tenant-b": {"job_held"}}
#: Answered by its upstream in SEP-2663's flat task shape.
FLAT_TOOL = "flat_job"
#: Served by an upstream that creates a task only for a caller that declared the
#: tasks extension, as SEP-2663 says one does.
SPEC_TOOL = "spec_job"
#: What a caller that did not declare the tasks extension is told, on each path.
CANNOT_POLL = {"front_door": "io.modelcontextprotocol/tasks", "egress": "TasksNotNegotiated"}


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
    tmp = tmp_path_factory.mktemp("front-door-tasks")
    with ThreadPoolExecutor(max_workers=len(TOPOLOGIES)) as pool:
        pending = {topology: pool.submit(_run, topology, tmp) for topology in TOPOLOGIES}
        return {topology: future.result() for topology, future in pending.items()}


def _calls(runs: dict[str, dict[str, Any]], topology: str, tenant: str) -> dict[str, dict[str, Any]]:
    return runs[topology]["calls"][tenant]


def _refused(runs: dict[str, dict[str, Any]], topology: str, tenant: str) -> set[str]:
    return {tool for tool, call in _calls(runs, topology, tenant).items() if call["outcome"] != "task"}


@pytest.mark.parametrize("tenant", TENANTS)
class TestTheFrontDoorRefusesWhatHangarCallRefuses:
    def test_the_same_calls_are_refused(self, runs, tenant: str) -> None:
        assert _refused(runs, "front_door", tenant) == REFUSED[tenant], runs["front_door"]["hangar"]
        assert _refused(runs, "egress", tenant) == REFUSED[tenant]

    @pytest.mark.parametrize("topology", TOPOLOGIES)
    def test_a_refused_call_never_reaches_the_upstream(self, runs, tenant: str, topology: str) -> None:
        reached = {tool for tool, call in _calls(runs, topology, tenant).items() if call["reached_upstream"]}

        assert reached == set(TOOLS) - REFUSED[tenant]


class TestAnApprovalListHoldsAFlatCall:
    @pytest.mark.parametrize("tenant", TENANTS)
    def test_a_held_call_is_refused_once_nobody_approves_it(self, runs, tenant: str) -> None:
        for tool in HELD[tenant]:
            call = _calls(runs, "front_door", tenant)[tool]

            # What the executor answers an approval that timed out, as a tool error.
            assert call == {"outcome": "refused", "detail": "No response within timeout", "reached_upstream": False}

    def test_a_tenant_approval_list_holds_that_tenant_alone(self, runs) -> None:
        assert _calls(runs, "front_door", "tenant-a")["solo_held_a"]["outcome"] == "refused"
        assert _calls(runs, "front_door", "tenant-b")["solo_held_a"]["outcome"] == "task"

    def test_hangar_call_is_held_the_same(self, runs) -> None:
        for tenant in TENANTS:
            for tool in HELD[tenant]:
                assert _calls(runs, "egress", tenant)[tool] == {
                    "outcome": "refused",
                    "detail": "approval_timeout",
                    "reached_upstream": False,
                }


@pytest.mark.parametrize("tenant", TENANTS)
class TestADeniedOrWithdrawnToolIsNotFoundOnTheFrontDoor:
    def test_it_is_not_found(self, runs, tenant: str) -> None:
        """Not shown and not callable are one decision on a front door."""
        for tool in REFUSED[tenant] - HELD[tenant]:
            assert _calls(runs, "front_door", tenant)[tool]["outcome"] == "error -32601", tool


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("topology", TOPOLOGIES)
class TestAnAllowedCallIsATaskItsCallerCanPoll:
    def test_the_seam_governed_it(self, runs, topology: str, tenant: str) -> None:
        """Positive control: the calls no gate refuses do come back through the relay seam."""
        for tool, call in _calls(runs, topology, tenant).items():
            if tool in REFUSED[tenant]:
                continue
            assert call["outcome"] == "task", (tool, call)
            assert call["polled"] == "working", (tool, call)


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("topology", TOPOLOGIES)
class TestAFlatShapedUpstreamTaskIsGovernedAlike:
    def test_its_caller_is_handed_it_and_can_poll_it(self, runs, topology: str, tenant: str) -> None:
        call = _calls(runs, topology, tenant)[FLAT_TOOL]

        assert call["outcome"] == "task", call
        assert call["polled"] == "working", call


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("topology", TOPOLOGIES)
class TestACallerThatCannotPollIsNotHandedATask:
    def test_it_is_refused_once_the_upstream_made_the_task(self, runs, topology: str, tenant: str) -> None:
        """The upstream was reached, so the refusal is the relay's, not a gate's."""
        undeclared = runs[topology]["undeclared"][tenant]

        assert set(undeclared) == {"job", FLAT_TOOL}
        for tool, call in undeclared.items():
            assert call["outcome"] == "refused", (tool, call)
            assert call["reached_upstream"], (tool, call)

    def test_it_is_told_what_to_declare(self, runs, topology: str, tenant: str) -> None:
        for tool, call in runs[topology]["undeclared"][tenant].items():
            assert CANNOT_POLL[topology] in call["detail"], (tool, call)


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("topology", TOPOLOGIES)
class TestACurrentSpecUpstreamMakesATaskForADeclaringCaller:
    """The caller's declaration has to reach the upstream for any of this to run (#1492).

    `job-spec` creates a task only for a caller that declared the tasks
    extension. On the front door the flat call ran the executor without the
    request context, so nothing was forwarded and this upstream answered an
    ordinary tool result -- the whole relay was unreachable through a front door
    for every spec-following upstream.
    """

    def test_a_declaring_caller_is_handed_a_task_it_can_poll(self, runs, topology: str, tenant: str) -> None:
        call = _calls(runs, topology, tenant)[SPEC_TOOL]

        assert call["outcome"] == "task", call
        assert call["polled"] == "working", call

    def test_a_caller_that_declared_nothing_gets_no_task(self, runs, topology: str, tenant: str) -> None:
        """The control: the upstream is reached either way, and answers differently.

        Without it, a task could be explained by an upstream that creates one
        unasked rather than by anything Hangar forwarded.
        """
        call = runs[topology]["spec_undeclared"][tenant]

        assert call["reached_upstream"], call
        assert call["outcome"] == "ok", call


@pytest.mark.parametrize("topology", TOPOLOGIES)
class TestATaskNoCallerIsHandedIsCancelledUpstream:
    """A task the relay refuses is one nobody will ever poll (#1492).

    The upstream has already created it, so left alone it runs to its own TTL
    producing a result no `tasks/*` call can reach. Each tenant's undeclared
    `job` and `flat_job` is one such task.
    """

    def test_each_refused_task_was_cancelled(self, runs, topology: str) -> None:
        cancelled = runs[topology]["cancelled"]

        assert len(cancelled) == 2 * len(TENANTS), cancelled

    def test_no_task_a_caller_holds_was_cancelled(self, runs, topology: str) -> None:
        """Only the unhanded ones: every recorded task is still the caller's to use."""
        assert set(runs[topology]["cancelled"]).isdisjoint(runs[topology]["recorded"])


@pytest.mark.parametrize("topology", TOPOLOGIES)
def test_the_store_records_exactly_the_tasks_callers_were_handed(runs, topology: str) -> None:
    handed = sorted(
        call["task_id"] for tenant in TENANTS for call in _calls(runs, topology, tenant).values() if call.get("task_id")
    )

    assert runs[topology]["recorded"] == handed
