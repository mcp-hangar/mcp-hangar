"""A call that names a group asks a human for the L7 rule of the member it is routed to (#1499).

The approval gate's L7 lookup keyed on the id the call NAMED. A group id is not
a server id -- the server repository holds no groups -- so a call naming a group
read no policy at all, and the `requireApproval` rule on the member it was
routed to never reached a human. The member's own L7 check then refused the
call on invoke: the operator configured "ask" and the caller got "refuse".

The governance decision now records the policy of the server the call is ROUTED
to: the one it names, or, for a group, the member `_gate_resolve_target`
selected. The selection itself is made before the decision and is not repeated
here, so the decision stays side-effect-free and can be made twice inside
`read_as_one_set` (#1431).

The invoke-time L7 check is untouched: it still refuses first, and every
refusal code and message is the one it was.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.approvals.models import ApprovalResult
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.policies.egress_l7 import L7Policy
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.server.tools.batch.executor import (
    BatchExecutor,
    _approval_loop_local,
    _decide_governance,
    _l7_policy_of,
)

GROUP = "math-pool"
#: The member the group selects. Its policy routes `store_secret` to a human.
MEMBER = "math-a"
#: A member with no L7 policy of its own.
SIBLING = "math-b"
#: On the member's `requireApproval` list.
HELD = "store_secret"
#: Allowed by the same policy, so nothing asks about it.
FREE = "add"

_POLICY = L7Policy.from_dict(
    {
        "tools": {"requireApproval": ["store_*"]},
        "defaultAction": "Allow",
        "mode": "Enforce",
    }
)


def _server(server_id: str, policy: L7Policy | None = None) -> McpServer:
    server = McpServer(mcp_server_id=server_id, mode="remote", endpoint="https://up.example/mcp")
    if policy is not None:
        server.set_l7_policy(policy)
    return server


class _Repo:
    """The server repository, as the call's context exposes it: servers, and no groups."""

    def __init__(self, servers: dict[str, McpServer]) -> None:
        self._servers = servers

    def get(self, mcp_server_id: str) -> McpServer | None:
        return self._servers.get(mcp_server_id)


@pytest.fixture
def repo() -> _Repo:
    return _Repo({MEMBER: _server(MEMBER, _POLICY), SIBLING: _server(SIBLING)})


@pytest.fixture(autouse=True)
def _reset_singletons() -> Any:
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


def _governance(named: str, *, is_group: bool, target: str, repo: _Repo, tool: str = HELD) -> Any:
    """The decision the executor makes for a call of *tool* on *named*, routed to *target*."""
    return _decide_governance(
        get_tool_access_resolver(),
        get_tool_projection_registry(),
        named,
        tool,
        None,
        is_group=is_group,
        target_server_id=target,
        servers=repo,
    )


def _call(named: str, tool: str) -> Any:
    return SimpleNamespace(mcp_server=named, tool=tool, arguments={}, call_id="c1", index=0)


def _executor() -> BatchExecutor:
    return object.__new__(BatchExecutor)


def _unrestricted_resolver() -> Any:
    """No MRTR approval list anywhere: the L7 rule is the only thing that can ask."""
    policy = SimpleNamespace(is_unrestricted=lambda: True, requires_approval=lambda _tool: False)
    return SimpleNamespace(resolve_effective_policy=lambda *_scope, **_member: policy)


class _Gate:
    """Records the policy it was asked with and answers a fixed result."""

    def __init__(self, result: ApprovalResult) -> None:
        self._result = result
        self.asked_with: dict[str, Any] | None = None

    async def check(self, **kwargs: Any) -> ApprovalResult:
        self.asked_with = kwargs
        return self._result


def _ctx(repo: _Repo, gate: _Gate | None) -> Any:
    return SimpleNamespace(repository=repo, approval_gate=gate)


class TestTheDecisionRecordsThePolicyOfTheServerTheCallIsRoutedTo:
    """What `_decide_governance` reads, which is the only thing the gate acts on."""

    def test_the_group_id_itself_names_no_server(self, repo: _Repo) -> None:
        """The premise: looking the group up in the server repository is what found nothing."""
        assert _l7_policy_of(repo, GROUP) is None

    def test_a_group_call_reads_the_selected_members_policy(self, repo: _Repo) -> None:
        decided = _governance(GROUP, is_group=True, target=MEMBER, repo=repo)

        assert decided.l7_policy is repo.get(MEMBER).l7_policy

    def test_another_member_of_the_same_group_is_read_when_it_is_the_one_selected(self, repo: _Repo) -> None:
        """The policy follows the selection, not the group: `math-b` declares none."""
        decided = _governance(GROUP, is_group=True, target=SIBLING, repo=repo)

        assert decided.l7_policy is None

    def test_a_call_naming_the_member_reads_its_own_policy(self, repo: _Repo) -> None:
        decided = _governance(MEMBER, is_group=False, target=MEMBER, repo=repo)

        assert decided.l7_policy is repo.get(MEMBER).l7_policy

    def test_a_group_with_no_member_selected_falls_back_to_the_id_the_call_named(self, repo: _Repo) -> None:
        """Nothing to read a policy from, and no exception: the gate is simply not routed."""
        decided = _governance(GROUP, is_group=True, target="", repo=repo)

        assert decided.l7_policy is None

    def test_the_decision_is_side_effect_free(self, repo: _Repo) -> None:
        """`read_as_one_set` may make it twice, so twice must read the same thing."""
        first = _governance(GROUP, is_group=True, target=MEMBER, repo=repo)
        second = _governance(GROUP, is_group=True, target=MEMBER, repo=repo)

        assert first.l7_policy is second.l7_policy is repo.get(MEMBER).l7_policy


class TestAGroupCallIsSentForApproval:
    def test_an_approved_call_passes_the_gate_carrying_its_approval_id(self, repo: _Repo) -> None:
        gate = _Gate(ApprovalResult.granted("appr-1"))
        decided = _governance(GROUP, is_group=True, target=MEMBER, repo=repo)

        result = _executor()._check_approval_gate(
            _call(GROUP, HELD), _unrestricted_resolver(), _ctx(repo, gate), governance=decided
        )

        assert result is None
        assert gate.asked_with is not None, "the member's requireApproval rule never reached a human"
        assert gate.asked_with["policy"].requires_approval(HELD)
        assert _approval_loop_local.approval_id == "appr-1"

    def test_a_denied_approval_refuses_the_call(self, repo: _Repo) -> None:
        gate = _Gate(ApprovalResult.denied("appr-2", "no"))
        decided = _governance(GROUP, is_group=True, target=MEMBER, repo=repo)

        result = _executor()._check_approval_gate(
            _call(GROUP, HELD), _unrestricted_resolver(), _ctx(repo, gate), governance=decided
        )

        assert result is not None and result.success is False
        assert result.error_type == "approval_denied"

    def test_an_approval_nobody_answers_refuses_the_call(self, repo: _Repo) -> None:
        gate = _Gate(ApprovalResult.expired("appr-3"))
        decided = _governance(GROUP, is_group=True, target=MEMBER, repo=repo)

        result = _executor()._check_approval_gate(
            _call(GROUP, HELD), _unrestricted_resolver(), _ctx(repo, gate), governance=decided
        )

        assert result is not None and result.success is False
        assert result.error_type == "approval_timeout"

    def test_with_no_gate_configured_it_stays_fail_closed(self, repo: _Repo) -> None:
        """Unchanged: the aggregate refuses on invoke. The gate check must not fabricate a pass."""
        decided = _governance(GROUP, is_group=True, target=MEMBER, repo=repo)

        result = _executor()._check_approval_gate(
            _call(GROUP, HELD), _unrestricted_resolver(), _ctx(repo, None), governance=decided
        )

        assert result is None
        assert getattr(_approval_loop_local, "approval_id", None) is None

    def test_a_tool_the_members_policy_allows_is_not_held(self, repo: _Repo) -> None:
        gate = _Gate(ApprovalResult.granted("appr-4"))
        decided = _governance(GROUP, is_group=True, target=MEMBER, repo=repo, tool=FREE)

        result = _executor()._check_approval_gate(
            _call(GROUP, FREE), _unrestricted_resolver(), _ctx(repo, gate), governance=decided
        )

        assert result is None
        assert gate.asked_with is None

    def test_a_group_routed_to_a_member_with_no_policy_is_not_held(self, repo: _Repo) -> None:
        gate = _Gate(ApprovalResult.granted("appr-5"))
        decided = _governance(GROUP, is_group=True, target=SIBLING, repo=repo)

        result = _executor()._check_approval_gate(
            _call(GROUP, HELD), _unrestricted_resolver(), _ctx(repo, gate), governance=decided
        )

        assert result is None
        assert gate.asked_with is None


class TestACallNamingTheMemberBehavesAsBefore:
    def test_it_is_sent_for_approval(self, repo: _Repo) -> None:
        gate = _Gate(ApprovalResult.granted("appr-6"))
        decided = _governance(MEMBER, is_group=False, target=MEMBER, repo=repo)

        result = _executor()._check_approval_gate(
            _call(MEMBER, HELD), _unrestricted_resolver(), _ctx(repo, gate), governance=decided
        )

        assert result is None
        assert gate.asked_with is not None
        assert _approval_loop_local.approval_id == "appr-6"

    def test_a_denied_approval_refuses_it(self, repo: _Repo) -> None:
        gate = _Gate(ApprovalResult.denied("appr-7", "no"))
        decided = _governance(MEMBER, is_group=False, target=MEMBER, repo=repo)

        result = _executor()._check_approval_gate(
            _call(MEMBER, HELD), _unrestricted_resolver(), _ctx(repo, gate), governance=decided
        )

        assert result is not None and result.error_type == "approval_denied"

    def test_a_server_with_no_policy_is_not_held(self, repo: _Repo) -> None:
        gate = _Gate(ApprovalResult.granted("appr-8"))
        decided = _governance(SIBLING, is_group=False, target=SIBLING, repo=repo)

        result = _executor()._check_approval_gate(
            _call(SIBLING, HELD), _unrestricted_resolver(), _ctx(repo, gate), governance=decided
        )

        assert result is None
        assert gate.asked_with is None
