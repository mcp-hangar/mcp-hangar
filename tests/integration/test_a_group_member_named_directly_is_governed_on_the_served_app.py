"""A group member named directly is governed by its group, on the app ``serve --http`` serves.

A group member is in the server repository like any other server, so
``hangar_call`` accepts its id. Before this fix, a call that named a member
instead of its group was governed as if the member were standalone. The group's
policy, its per-member policy, and its withdrawals and pin were never consulted.

Each mode runs ``_member_direct_governance_harness.py`` in a fresh interpreter:
the real ``bootstrap()`` over a config dict, ``hangar_call`` through the served
app, and the mock provider over stdio. ``open`` runs with auth off. ``auth``
runs behind the served auth enforcement, with API keys for two tenants whose
principal holds ``tool:invoke``. Nothing in the policy, withdrawal or pin path
is mocked or registered by hand: the config file shape is what declares all of
it.

The group ``math-pool`` has members ``math-a`` and ``math-b``. It denies
``multiply``, withdraws ``subtract`` for every tenant and ``power`` for
``tenant-a``, and pins ``divide`` to a digest nothing matches. Its spec also
gives ``math-a`` a member-level deny on ``echo``. ``math-solo`` is in no group.

``approval`` mode adds two approval lists, each with a one-second timeout: one
on the group for ``add``, and one on ``math-solo`` for ``tenant-a`` and
``echo``. The approval gate used to read only the named server's own list.

``l7`` mode puts an L7 egress policy on ``math-a``, the member the group
selects, whose ``requireApproval`` rule covers ``add`` and ``power``, and
staffs the gate with an approver who grants ``add`` and denies ``power``. The
gate used to look that policy up by the id the call named, and a group id is
not a server id, so a call naming the group asked nobody and was refused at
invoke by the member's own check (#1499).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_member_direct_governance_harness.py")
MODES = ("open", "auth", "approval", "l7")
TIMED_OUT = "approval_timeout"
#: What a call refused by the approver is told.
APPROVER_SAID_NO = "approval_denied"
#: The two tools `math-a`'s L7 policy routes to a human in `l7` mode.
L7_GRANTED, L7_DENIED = "add", "power"

# As `_member_direct_governance_harness.py` names them.
GROUP, MEMBER, SIBLING, SOLO = "math-pool", "math-a", "math-b", "math-solo"
TOOLS = ("add", "multiply", "subtract", "divide", "power", "echo")

DENIED, WITHDRAWN, MISMATCH = "ToolAccessDeniedError", "ToolWithdrawnError", "ToolDigestMismatchError"

#: What the group's own declarations refuse. `echo` is left out: `math-a`'s
#: member-level deny refuses it, and the group routes to `math-a` first.
GROUP_REFUSES = {"multiply": DENIED, "subtract": WITHDRAWN, "divide": MISMATCH}
ALL_OK = dict.fromkeys(TOOLS, "ok")


def _run(mode: str, tmp: Path) -> dict[str, Any]:
    out = tmp / mode / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(out)],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{mode}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return json.loads(out.read_text())


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    # Concurrently, under the 60s pytest-timeout the integration job applies.
    tmp = tmp_path_factory.mktemp("member-direct")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


def _outcomes(runs: dict[str, dict[str, Any]], mode: str, tenant: str = "-") -> dict[str, dict[str, str]]:
    return runs[mode]["outcomes"][tenant]


class TestWithAuthOff:
    def test_the_group_is_refused_what_it_declares(self, runs) -> None:
        assert _outcomes(runs, "open")[GROUP] == {**ALL_OK, **GROUP_REFUSES, "echo": DENIED}

    def test_a_member_named_directly_is_refused_the_same(self, runs) -> None:
        outcomes = _outcomes(runs, "open")

        assert outcomes[MEMBER] == outcomes[GROUP], runs["open"]["hangar"]

    def test_every_member_is_covered(self, runs) -> None:
        """`math-b` has no member-level policy, so only `echo` differs from `math-a`."""
        assert _outcomes(runs, "open")[SIBLING] == {**ALL_OK, **GROUP_REFUSES}

    def test_a_per_tenant_withdrawal_does_not_refuse_a_caller_with_no_tenant(self, runs) -> None:
        outcomes = _outcomes(runs, "open")

        assert (outcomes[GROUP]["power"], outcomes[MEMBER]["power"]) == ("ok", "ok")

    def test_a_server_in_no_group_is_untouched(self, runs) -> None:
        assert _outcomes(runs, "open")[SOLO] == ALL_OK


class TestWithAuthOnAndToolInvoke:
    def test_the_tenant_the_group_withdrew_a_tool_for_is_refused_it_on_the_member(self, runs) -> None:
        outcomes = _outcomes(runs, "auth", "tenant-a")

        assert outcomes[GROUP]["power"] == WITHDRAWN
        assert outcomes[MEMBER]["power"] == WITHDRAWN, runs["auth"]["hangar"]

    def test_another_tenant_is_not(self, runs) -> None:
        outcomes = _outcomes(runs, "auth", "tenant-b")

        assert (outcomes[GROUP]["power"], outcomes[MEMBER]["power"]) == ("ok", "ok")

    @pytest.mark.parametrize("tenant", ["tenant-a", "tenant-b"])
    def test_a_member_named_directly_is_refused_what_the_group_is(self, runs, tenant: str) -> None:
        outcomes = _outcomes(runs, "auth", tenant)

        assert outcomes[GROUP]["add"] == "ok", "the key's tool:invoke let the call through"
        assert outcomes[MEMBER] == outcomes[GROUP]

    @pytest.mark.parametrize("tenant", ["tenant-a", "tenant-b"])
    def test_a_server_in_no_group_is_untouched(self, runs, tenant: str) -> None:
        assert _outcomes(runs, "auth", tenant)[SOLO] == ALL_OK


class TestApprovalListsWithAuthOn:
    """The approval gate reads the group's list and the caller's tenant list.

    Nobody answers, so a held call ends in ``approval_timeout`` after the one
    second the lists declare. Before the fix, the gate read only the named
    server's own list, so none of these calls was held.
    """

    def test_a_group_approval_list_holds_the_group_and_a_member_named_directly(self, runs) -> None:
        outcomes = _outcomes(runs, "approval", "tenant-a")

        assert (outcomes[GROUP]["add"], outcomes[MEMBER]["add"]) == (TIMED_OUT, TIMED_OUT), runs["approval"]["hangar"]

    def test_a_tenant_approval_list_holds_that_tenant(self, runs) -> None:
        assert _outcomes(runs, "approval", "tenant-a")[SOLO]["echo"] == TIMED_OUT

    def test_it_does_not_hold_another_tenant(self, runs) -> None:
        assert _outcomes(runs, "approval", "tenant-b")[SOLO]["echo"] == "ok"

    def test_a_tool_on_no_approval_list_is_not_held(self, runs) -> None:
        assert _outcomes(runs, "approval", "tenant-a")[SOLO]["add"] == "ok"


class TestAnL7RuleOnTheSelectedMemberReachesAHuman:
    """A call naming the group is sent for approval under the member's rule (#1499).

    An approver answers in the harness: ``add`` granted, ``power`` denied.
    Before the fix, a call naming the group read no L7 policy at all, so the
    gate asked nobody and the member's own check refused the call at invoke.
    """

    def test_a_group_call_runs_once_the_members_rule_is_approved(self, runs) -> None:
        assert _outcomes(runs, "l7")[GROUP][L7_GRANTED] == "ok", runs["l7"]["hangar"]

    def test_a_denied_approval_refuses_the_group_call(self, runs) -> None:
        assert _outcomes(runs, "l7")[GROUP][L7_DENIED] == APPROVER_SAID_NO

    def test_a_call_naming_the_member_behaves_as_before(self, runs) -> None:
        outcomes = _outcomes(runs, "l7")

        assert outcomes[MEMBER] == {L7_GRANTED: "ok", L7_DENIED: APPROVER_SAID_NO}
        assert outcomes[GROUP] == outcomes[MEMBER]

    def test_the_sibling_that_declares_no_policy_is_untouched(self, runs) -> None:
        """The rule read is the selected member's, not any member's."""
        assert _outcomes(runs, "l7")[SIBLING] == {L7_GRANTED: "ok", L7_DENIED: "ok"}
