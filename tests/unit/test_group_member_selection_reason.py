"""A group's selection says why it chose the member it did (#1286, ADR-029 s5).

`select_member_for` returned only a member or None, so a pinned tenant, a
canary split, a canary that fell back and a plain load-balanced pick could not
be told apart. Selection advances the load balancer, so the reason has to come
out of the same call that chose the member: a telemetry-only second selection
could pick another one. These tests pin one reason per branch of
`select_member_with_reason`, and that `select_member_for` still routes exactly
as it did.

Naming: neutral placeholders only (pool, member-a, member-b, tenant-*).
"""

from __future__ import annotations

import pytest

from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import (
    CanaryPolicy,
    LoadBalancerStrategy,
    McpServerGroup,
    MemberSelection,
    RouteReason,
)

_A, _B, _C = "member-a", "member-b", "member-c"
_TENANT = "tenant-x"


def _group(*, strategy: LoadBalancerStrategy = LoadBalancerStrategy.PRIORITY, out: tuple[str, ...] = ()):
    """`pool` = {member-a, member-b, member-c}, first member first, each in rotation except *out*."""
    group = McpServerGroup(group_id="pool", strategy=strategy, auto_start=False)
    for priority, server_id in enumerate((_A, _B, _C), start=1):
        group.add_member(McpServer(mcp_server_id=server_id, mode="subprocess", command=["unused"]), priority=priority)
        member = group.get_member(server_id)
        assert member is not None
        member.in_rotation = server_id not in out
    return group


def _chosen(selection: MemberSelection) -> tuple[str | None, RouteReason]:
    return (str(selection.member.id) if selection.member is not None else None, selection.reason)


class TestEachBranchNamesItsReason:
    def test_a_tenant_with_no_canary_policy_is_load_balanced(self):
        assert _chosen(_group().select_member_with_reason(_TENANT)) == (_A, RouteReason.LOAD_BALANCED)

    def test_a_call_with_no_tenant_is_load_balanced_even_under_a_canary_policy(self):
        group = _group()
        group.set_canary_policy(CanaryPolicy(canary_member=_B, split_pct=100, pinned_tenants={_TENANT: _C}))

        assert _chosen(group.select_member_with_reason(None)) == (_A, RouteReason.LOAD_BALANCED)

    def test_a_pinned_tenant_reads_pinned(self):
        group = _group()
        group.set_canary_policy(CanaryPolicy(pinned_tenants={_TENANT: _C}))

        assert _chosen(group.select_member_with_reason(_TENANT)) == (_C, RouteReason.PINNED)

    def test_a_pin_wins_over_the_split(self):
        group = _group()
        group.set_canary_policy(CanaryPolicy(canary_member=_B, split_pct=100, pinned_tenants={_TENANT: _C}))

        assert _chosen(group.select_member_with_reason(_TENANT)) == (_C, RouteReason.PINNED)

    def test_a_tenant_in_the_split_reads_canary(self):
        group = _group()
        group.set_canary_policy(CanaryPolicy(canary_member=_B, split_pct=100))

        assert _chosen(group.select_member_with_reason(_TENANT)) == (_B, RouteReason.CANARY)

    def test_a_tenant_outside_the_split_is_load_balanced(self):
        group = _group()
        # A zero split sends nobody to the canary: the tenant is outside it.
        group.set_canary_policy(CanaryPolicy(canary_member=_B, split_pct=0, pinned_tenants={"tenant-other": _C}))

        assert _chosen(group.select_member_with_reason(_TENANT)) == (_A, RouteReason.LOAD_BALANCED)

    def test_a_canary_out_of_rotation_falls_back(self):
        group = _group(out=(_B,))
        group.set_canary_policy(CanaryPolicy(canary_member=_B, split_pct=100))

        assert _chosen(group.select_member_with_reason(_TENANT)) == (_A, RouteReason.CANARY_FALLBACK)

    def test_a_pin_out_of_rotation_falls_back_as_a_canary_does(self):
        """Decision 2 on #1286: no separate value for a pin; pins live under `canary:`."""
        group = _group(out=(_C,))
        group.set_canary_policy(CanaryPolicy(pinned_tenants={_TENANT: _C}))

        assert _chosen(group.select_member_with_reason(_TENANT)) == (_A, RouteReason.CANARY_FALLBACK)

    def test_a_pin_to_a_member_the_group_does_not_have_falls_back(self):
        group = _group()
        group.set_canary_policy(CanaryPolicy(pinned_tenants={_TENANT: "member-gone"}))

        assert _chosen(group.select_member_with_reason(_TENANT)) == (_A, RouteReason.CANARY_FALLBACK)

    def test_no_member_in_rotation_reads_no_available_member(self):
        group = _group(out=(_A, _B, _C))
        group.set_canary_policy(CanaryPolicy(pinned_tenants={_TENANT: _A}))

        assert _chosen(group.select_member_with_reason(_TENANT)) == (None, RouteReason.NO_AVAILABLE_MEMBER)

    def test_the_vocabulary_is_the_six_values_adr_029_names(self):
        assert {r.value for r in RouteReason} == {
            "standalone",
            "load_balanced",
            "pinned",
            "canary",
            "canary_fallback",
            "no_available_member",
        }


class TestRoutingIsUnchanged:
    @pytest.mark.parametrize("strategy", [s for s in LoadBalancerStrategy if s is not LoadBalancerStrategy.RANDOM])
    def test_select_member_for_picks_the_same_sequence_as_the_reasoned_selection(self, strategy):
        """Two identical groups, one asked each way: the same members, in the same order. Random has no order."""
        plain, reasoned = _group(strategy=strategy), _group(strategy=strategy)

        picked = [str(plain.select_member_for(_TENANT).id) for _ in range(7)]
        explained = [str(reasoned.select_member_with_reason(_TENANT).member.id) for _ in range(7)]

        assert picked == explained

    def test_select_member_for_returns_none_when_nothing_is_selectable(self):
        assert _group(out=(_A, _B, _C)).select_member_for(_TENANT) is None

    @pytest.mark.parametrize("tenant", ["tenant-x", "tenant-y", "tenant-pinned", "t-0", "t-1", "t-2", "t-3"])
    def test_canary_policy_resolve_still_answers_only_the_member(self, tenant):
        policy = CanaryPolicy(canary_member=_B, split_pct=40, pinned_tenants={"tenant-pinned": _C})

        resolved = policy.resolve_with_reason(tenant)

        assert policy.resolve(tenant) == (resolved[0] if resolved else None)
