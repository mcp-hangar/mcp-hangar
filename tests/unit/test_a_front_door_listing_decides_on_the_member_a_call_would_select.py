"""A front-door listing decides each name on the member a call would be routed to (#1784, D4 and D5).

PR 3 of the plan on #1763. The flat tool map used to keep a name when the
member whose projection carried it allowed it, whichever member a call then
selected (D4), and a prompt or a resource of a group was decided under the
group alone, so a member-level policy never applied, and relayed to a member
chosen without the tenant (D5). Now every surface decides on the members a
call for this tenant may be routed to, through `McpServerGroup.route_candidates`,
which selects nothing and starts nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import CanaryPolicy, McpServerGroup
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects.access import Target
from mcp_hangar.domain.value_objects.mcp_server import McpServerState
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.fastmcp_server import flat_tool_projection as ftp
from mcp_hangar.fastmcp_server import prompt_proxy as pp

GROUP, A, B = "pool", "member-a", "member-b"
PINNED, OTHER = "tenant:pinned", "tenant:other"


@pytest.fixture(autouse=True)
def _clean() -> Any:
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    yield
    reset_tool_access_resolver()
    reset_tool_projection_registry()


def _server(server_id: str) -> McpServer:
    return McpServer(mcp_server_id=server_id, mode="subprocess", command=["unused"], metrics_publisher=Mock())


def _pool(*in_rotation: str, pinned: dict[str, str] | None = None) -> McpServerGroup:
    """`member-a` and `member-b`, never started; *in_rotation* names the selectable ones."""
    group = McpServerGroup(GROUP, auto_start=False)
    for server_id in (A, B):
        group.add_member(_server(server_id))
        group.get_member(server_id).in_rotation = server_id in in_rotation  # type: ignore[union-attr]
    if pinned:
        group.set_canary_policy(CanaryPolicy(pinned_tenants=pinned))
    return group


class TestRouteCandidates:
    """The side-effect-free selection a listing decides on."""

    def test_a_load_balanced_tenant_may_be_routed_to_every_selectable_member(self) -> None:
        assert [str(m.id) for m in _pool(A, B).route_candidates(OTHER)] == [A, B]

    def test_a_member_out_of_rotation_is_not_a_candidate(self) -> None:
        assert [str(m.id) for m in _pool(B).route_candidates(OTHER)] == [B]

    def test_a_pinned_tenant_is_routed_to_its_member_alone(self) -> None:
        assert [str(m.id) for m in _pool(A, B, pinned={PINNED: B}).route_candidates(PINNED)] == [B]

    def test_a_pin_to_a_member_out_of_rotation_falls_back_to_the_load_balancer(self) -> None:
        """As `select_member_with_reason` falls back: routing never sends traffic out of rotation."""
        assert [str(m.id) for m in _pool(A, pinned={PINNED: B}).route_candidates(PINNED)] == [A]

    def test_no_member_in_rotation_means_no_candidate(self) -> None:
        group = _pool()

        assert group.route_candidates(OTHER) == ()
        assert group.select_member_for(OTHER) is None, "and a call to the group is refused"

    def test_it_selects_nothing(self) -> None:
        """Asked twice, it answers the same and moves no clock; a selection would."""
        group = _pool(A, B)
        before = [group.get_member(m).last_selected_at for m in (A, B)]  # type: ignore[union-attr]

        first, second = group.route_candidates(OTHER), group.route_candidates(OTHER)

        assert first == second
        assert [group.get_member(m).last_selected_at for m in (A, B)] == before  # type: ignore[union-attr]
        group.select_member_for(OTHER)
        assert [group.get_member(m).last_selected_at for m in (A, B)] != before  # type: ignore[union-attr]


def _topology(group: McpServerGroup) -> Any:
    return patch.dict("mcp_hangar.server.bootstrap.composition.GROUPS", {GROUP: group}, clear=True)


class TestTheTargetsACallResolvesTo:
    def test_a_member_of_one_group_is_decided_as_the_call_to_its_group(self) -> None:
        with _topology(_pool(A, B)):
            assert ftp._targets_of_a_call(A, OTHER, "tool") == (
                Target.group(GROUP, member_server=A),
                Target.group(GROUP, member_server=B),
            )

    def test_a_group_id_is_decided_on_the_member_the_tenant_is_routed_to(self) -> None:
        with _topology(_pool(A, B, pinned={PINNED: B})):
            routed = Target.group(GROUP, member_server=B, kind="prompt")

            assert ftp._targets_of_a_call(GROUP, PINNED, "prompt") == (routed,)

    def test_a_group_with_no_member_in_rotation_has_no_target(self) -> None:
        with _topology(_pool()):
            assert ftp._targets_of_a_call(GROUP, OTHER, "tool") == ()
            assert not ftp.is_governed_allowed(GROUP, "anything", kind="tool", tenant_id=OTHER)

    def test_a_server_in_no_group_is_itself(self) -> None:
        with _topology(_pool(A, B)):
            assert ftp._targets_of_a_call("solo", OTHER, "resource") == (Target.server("solo", kind="resource"),)


class TestD4AMemberOfOneGroupOnTheFrontDoor:
    """A name is listed only when every member a call for this tenant may select allows it."""

    @pytest.fixture(autouse=True)
    def _policies(self) -> None:
        resolver = get_tool_access_resolver()
        resolver.set_member_policy(GROUP, B, ToolAccessPolicy(deny_list=("admin_*",)), mcp_server_id=B)

    def test_a_tool_one_member_denies_is_hidden_for_a_load_balanced_tenant(self) -> None:
        with _topology(_pool(A, B)):
            assert not ftp.is_governed_allowed(A, "admin_reset", kind="tool", tenant_id=OTHER)
            assert ftp.is_governed_allowed(A, "query_rows", kind="tool", tenant_id=OTHER)

    def test_it_is_listed_for_a_tenant_pinned_to_the_member_that_allows_it(self) -> None:
        with _topology(_pool(A, B, pinned={PINNED: A})):
            assert ftp.is_governed_allowed(A, "admin_reset", kind="tool", tenant_id=PINNED)
            assert not ftp.is_governed_allowed(A, "admin_reset", kind="tool", tenant_id=OTHER)

    def test_it_is_listed_once_the_denying_member_leaves_rotation(self) -> None:
        with _topology(_pool(A)):
            assert ftp.is_governed_allowed(A, "admin_reset", kind="tool", tenant_id=OTHER)

    def test_a_withdrawal_on_a_candidate_hides_the_name(self) -> None:
        get_tool_projection_registry().withdraw(B, "query_rows", None)

        with _topology(_pool(A, B)):
            assert not ftp.is_governed_allowed(A, "query_rows", kind="tool", tenant_id=OTHER)
        with _topology(_pool(A)):
            assert ftp.is_governed_allowed(A, "query_rows", kind="tool", tenant_id=OTHER)


class TestD5PromptsAndResourcesOfAGroup:
    """Decided on the members a call may select, and answered by one of them."""

    def test_a_member_level_prompt_policy_applies_to_the_group(self) -> None:
        get_tool_access_resolver().set_member_policy(
            GROUP, B, ToolAccessPolicy(deny_list=("draft_*",)), mcp_server_id=B, kind="prompt"
        )

        with _topology(_pool(A, B)):
            assert not ftp.is_governed_allowed(GROUP, "draft_email", kind="prompt", tenant_id=OTHER)
            assert ftp.is_governed_allowed(GROUP, "greet", kind="prompt", tenant_id=OTHER)
        with _topology(_pool(A, B, pinned={PINNED: A})):
            assert ftp.is_governed_allowed(GROUP, "draft_email", kind="prompt", tenant_id=PINNED)

    def test_a_listing_is_answered_by_a_running_candidate_without_a_selection(self) -> None:
        ready = SimpleNamespace(id=B, state_snapshot=McpServerState.READY)
        cold = SimpleNamespace(id=A, state_snapshot=McpServerState.COLD)
        group = SimpleNamespace(
            route_candidates=lambda tenant_id: (cold, ready),
            select_member_for=Mock(side_effect=AssertionError("a listing selects nothing")),
        )

        assert pp._member_answering(group, "prompts/list") is ready
        assert pp._member_answering(group, "resources/list") is ready
        assert pp._member_answering(group, "resources/templates/list") is ready

    def test_a_listing_with_no_running_candidate_is_answered_by_nobody(self) -> None:
        cold = SimpleNamespace(id=A, state_snapshot=McpServerState.COLD)
        group = SimpleNamespace(route_candidates=lambda tenant_id: (cold,), select_member_for=Mock())

        assert pp._member_answering(group, "prompts/list") is None
        group.select_member_for.assert_not_called()

    def test_a_fetch_selects_the_member_for_the_caller(self) -> None:
        selected = SimpleNamespace(id=B)
        group = SimpleNamespace(route_candidates=Mock(), select_member_for=Mock(return_value=selected))

        with patch.object(pp, "_caller_tenant", return_value=PINNED):
            assert pp._member_answering(group, "prompts/get") is selected
            assert pp._member_answering(group, "resources/read") is selected
            assert pp._member_answering(group, "completion/complete") is selected

        assert group.select_member_for.call_args_list == [((PINNED,),)] * 3
        group.route_candidates.assert_not_called()
