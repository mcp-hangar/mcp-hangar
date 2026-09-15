"""A front-door member of several groups is governed by each of them, as ``hangar_call`` governs it (#1474).

The front door kept one group per member, so a member of groups A and B was
governed by only one of them, whichever the file declared last. ``hangar_call``
naming the member was governed by both. The served check is
``tests/integration/test_a_front_door_member_of_several_groups_is_governed_by_each.py``;
this pins the decisions it rests on, in both config orders.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.policies.header_exposure import (
    clear_header_exposure_policies,
    HeaderExposurePolicy,
    set_header_exposure_policy,
)
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.fastmcp_server import flat_tool_projection as ftp
from mcp_hangar.server.state import GROUPS
from mcp_hangar.server.tools.batch import executor

SHARED, SINGLE = "shared-member", "single-member"
POOL_A, POOL_B, POOL_C = "pool-a", "pool-b", "pool-c"
TENANT, OTHER = "tenant-a", "tenant-b"


def _group(group_id: str, member: str) -> SimpleNamespace:
    return SimpleNamespace(id=group_id, members=[SimpleNamespace(id=member)])


@pytest.fixture(autouse=True)
def _clean_state():
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    yield
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    clear_header_exposure_policies()


@pytest.fixture(params=[(POOL_A, POOL_B), (POOL_B, POOL_A)], ids=["a-then-b", "b-then-a"])
def order(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """`shared-member` in pool-a and pool-b, declared in this order; `single-member` in pool-c alone."""
    for group_id in request.param:
        monkeypatch.setitem(GROUPS, group_id, _group(group_id, SHARED))
    monkeypatch.setitem(GROUPS, POOL_C, _group(POOL_C, SINGLE))
    resolver = get_tool_access_resolver()
    resolver.set_group_policy(POOL_A, ToolAccessPolicy(deny_list=("a_denied",)))
    resolver.set_group_policy(POOL_B, ToolAccessPolicy(deny_list=("b_denied",)))
    resolver.set_group_policy(POOL_C, ToolAccessPolicy(deny_list=("c_denied",)))
    return request.param


def _allowed(server: str, name: str, tenant: str = TENANT, kind: str = "tool") -> bool:
    return ftp.is_governed_allowed(server, name, kind=kind, tenant_id=tenant)  # type: ignore[arg-type]


def test_every_owner_is_kept_in_config_order(order) -> None:
    assert ftp._member_to_groups()[SHARED] == order
    assert executor._groups_owning(SHARED) == order, "hangar_call reads the same map"


def test_a_member_of_one_group_routes_through_it_and_a_member_of_several_to_itself(order) -> None:
    routes = ftp._member_to_group()

    assert routes[SINGLE] == POOL_C
    assert SHARED not in routes


def test_the_listing_asks_the_scopes_hangar_call_asks_for_the_member(order) -> None:
    assert ftp._policy_scopes(SHARED) == executor.member_policy_scopes(SHARED, order)


@pytest.mark.parametrize("tool", ["a_denied", "b_denied"])
def test_a_tool_either_group_denies_is_refused(order, tool: str) -> None:
    assert not _allowed(SHARED, tool)
    assert _allowed(SHARED, "shared_ok")


def test_a_tool_either_group_withdraws_is_refused(order) -> None:
    get_tool_projection_registry().withdraw(POOL_B, "b_withdrawn", None)

    assert (_allowed(SHARED, "b_withdrawn"), _allowed(SHARED, "b_withdrawn", OTHER)) == (False, False)


def test_a_withdrawal_for_one_tenant_refuses_only_that_tenant(order) -> None:
    get_tool_projection_registry().withdraw(POOL_B, "b_withdrawn_for_a", TENANT)

    assert (_allowed(SHARED, "b_withdrawn_for_a"), _allowed(SHARED, "b_withdrawn_for_a", OTHER)) == (False, True)


def test_a_prompt_on_the_member_is_governed_by_each_group(order) -> None:
    get_tool_access_resolver().set_group_policy(POOL_B, ToolAccessPolicy(deny_list=("draft_*",)), kind="prompt")

    assert not _allowed(SHARED, "draft_email", kind="prompt")
    assert _allowed(SHARED, "greet", kind="prompt")


def test_a_member_of_one_group_is_governed_by_that_group_alone(order) -> None:
    assert ftp._policy_scopes(SINGLE) == [(POOL_C, POOL_C, SINGLE)]
    assert not _allowed(SINGLE, "c_denied")
    assert (_allowed(SINGLE, "a_denied"), _allowed(SINGLE, "b_denied")) == (True, True)


def test_each_groups_header_exposure_block_applies_to_the_member(order) -> None:
    set_header_exposure_policy(POOL_A, HeaderExposurePolicy(("*token*",), "warn"))
    set_header_exposure_policy(POOL_B, HeaderExposurePolicy(("*token*",), "withdraw"))

    actions = {POOL_A: "warn", POOL_B: "withdraw"}
    blocks = ftp._group_exposure_policies(SHARED)

    assert [block.on_violation for block in blocks] == [actions[group_id] for group_id in order]
