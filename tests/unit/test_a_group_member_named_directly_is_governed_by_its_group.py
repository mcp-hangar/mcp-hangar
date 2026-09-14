"""A group member named directly is governed by its group.

A group member is in the server repository like any other server, so
`hangar_call` accepts its id. A call that named the member instead of the group
was governed as if the member were a standalone server:

- `_gate_resolve_target` found it as a plain server;
- `_gate_tool_access` asked the resolver with no group;
- the withdrawal and pin gates looked only under the member id.

A tool the operator denied, withdrew or pinned on the group therefore ran when
a caller named a member.

These tests drive `BatchExecutor.execute`, and the `hangar_call` tool function,
against the real resolver and projection registry, with real `McpServer` and
`McpServerGroup` objects in `GROUPS`. Each case asserts both directions: a call
naming the group and a call naming its member get the same answer. Two things
must not change: a server in no group is governed as before, and a call naming a
member is still sent to that member.

The last two classes pin two more gaps of the same kind. The approval gate read
only the named server's approval list, with no group and no tenant. The
re-check after an approval hold did not re-check withdrawal.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

import mcp_hangar.server.tools.batch as batch
from mcp_hangar.application.commands import InvokeToolCommand
from mcp_hangar.approvals.models import ApprovalResult
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import LoadBalancerStrategy, McpServerGroup
from mcp_hangar.domain.events import ToolWithdrawnRejected
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects import DigestEnforcement, ToolDigest
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec, hangar_call

_GROUP = "pool"
_OTHER_GROUP = "other-pool"
_MEMBER = "member-a"  # the group's first choice: priority 1
_SIBLING = "member-b"
_SOLO = "solo"  # in no group
_TOOL = "reset"  # the tool the operator restricts
_OPEN_TOOL = "read"  # a tool nothing restricts
_TENANT = "tenant-a"
_OTHER_TENANT = "tenant-b"
_STALE_DIGEST = "a" * 64  # never matches the real schema digest


def _identity(tenant_id: str) -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
        )
    )


@pytest.fixture(autouse=True)
def _reset_singletons():
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


def _server(server_id: str) -> McpServer:
    return McpServer(mcp_server_id=server_id, mode="subprocess", command=["unused"])


def _group(group_id: str, *servers: McpServer) -> McpServerGroup:
    """A group whose members are in rotation, first member first.

    Put in rotation the way a successful start does it
    (`_try_start_member_unlocked`), without starting anything.
    """
    group = McpServerGroup(group_id=group_id, strategy=LoadBalancerStrategy.PRIORITY, auto_start=False)
    for priority, server in enumerate(servers, start=1):
        group.add_member(server, priority=priority)
        member = group.get_member(str(server.id))
        assert member is not None
        member.in_rotation = True
    return group


@pytest.fixture()
def world():
    """`pool` = {member-a, member-b}, and `solo` in no group, every gate open.

    The servers are what `_load_group_members` puts in the repository: a member
    is a server there, which is how `hangar_call` came to accept its id.
    """
    servers = {server_id: _server(server_id) for server_id in (_MEMBER, _SIBLING, _SOLO)}
    groups = {_GROUP: _group(_GROUP, servers[_MEMBER], servers[_SIBLING])}
    for server_id in servers:
        get_tool_projection_registry().build_from_tools(
            server_id, [ToolSchema(name=name, description=name, input_schema={}) for name in (_TOOL, _OPEN_TOOL)]
        )

    context = Mock()
    context.command_bus.send.return_value = {"ok": True}
    context.governed_task_store = None
    context.approval_gate = None
    context.auth_components = None  # auth off
    # A group id is not a server id: that is what sends a call naming the
    # group down the group branch, exactly as in production.
    context.get_mcp_server.side_effect = servers.get
    context.mcp_server_exists.side_effect = lambda server_id: server_id in servers
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=context),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=context),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS", groups),
        patch("mcp_hangar.server.tools.batch.validator.GROUPS", groups),
    ):
        yield SimpleNamespace(context=context, servers=servers, groups=groups)


def _call(target: str, tool: str = _TOOL, *, tenant: str | None = _TENANT):
    token = identity_context_var.set(_identity(tenant) if tenant is not None else None)
    try:
        batch_result = BatchExecutor().execute(
            batch_id="b",
            calls=[CallSpec(index=0, call_id="c-1", mcp_server=target, tool=tool, arguments={})],
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )
    finally:
        identity_context_var.reset(token)
    return batch_result.results[0]


def _refusal(target: str, tool: str = _TOOL, *, tenant: str | None = _TENANT) -> str | None:
    """The error type a call gets, or None when it ran."""
    result = _call(target, tool, tenant=tenant)
    return None if result.success else result.error_type


def _dispatched_to(context: Mock) -> list[str]:
    return [
        c.args[0].mcp_server_id
        for c in context.command_bus.send.call_args_list
        if isinstance(c.args[0], InvokeToolCommand)
    ]


class TestAGroupDeny:
    def test_the_member_is_refused_as_the_group_is(self, world) -> None:
        get_tool_access_resolver().set_group_policy(_GROUP, ToolAccessPolicy(deny_list=(_TOOL,)))

        assert _refusal(_GROUP) == "ToolAccessDeniedError"
        assert _refusal(_MEMBER) == "ToolAccessDeniedError"
        assert _refusal(_SIBLING) == "ToolAccessDeniedError"

    def test_a_tool_the_group_does_not_deny_still_runs_on_the_member(self, world) -> None:
        get_tool_access_resolver().set_group_policy(_GROUP, ToolAccessPolicy(deny_list=(_TOOL,)))

        assert _refusal(_MEMBER, _OPEN_TOOL) is None
        assert _refusal(_GROUP, _OPEN_TOOL) is None

    def test_a_group_allow_list_holds_against_the_member(self, world) -> None:
        get_tool_access_resolver().set_group_policy(_GROUP, ToolAccessPolicy(allow_list=(_OPEN_TOOL,)))

        assert _refusal(_MEMBER) == "ToolAccessDeniedError"
        assert _refusal(_MEMBER, _OPEN_TOOL) is None

    def test_it_holds_for_a_caller_with_no_tenant(self, world) -> None:
        """Auth off: no caller carries a tenant, and the group deny still holds."""
        get_tool_access_resolver().set_group_policy(_GROUP, ToolAccessPolicy(deny_list=(_TOOL,)))

        assert _refusal(_GROUP, tenant=None) == "ToolAccessDeniedError"
        assert _refusal(_MEMBER, tenant=None) == "ToolAccessDeniedError"

    def test_a_tenant_policy_on_the_group_holds_against_the_member(self, world) -> None:
        """`tool_access.member.<tenant>` declared on the group, which a group call honours."""
        get_tool_access_resolver().set_standalone_member_policy(_GROUP, _TENANT, ToolAccessPolicy(deny_list=(_TOOL,)))

        assert _refusal(_GROUP) == "ToolAccessDeniedError"
        assert _refusal(_MEMBER) == "ToolAccessDeniedError"
        assert _refusal(_MEMBER, tenant=_OTHER_TENANT) is None


class TestAMemberPolicyInTheGroupSpec:
    """`groups.<g>.members.<m>.tools`, registered the way `_load_group_members` registers it."""

    @pytest.fixture(autouse=True)
    def _member_denies(self, world) -> None:
        get_tool_access_resolver().set_member_policy(
            group_id=_GROUP, member_id=_MEMBER, policy=ToolAccessPolicy(deny_list=(_TOOL,)), mcp_server_id=_MEMBER
        )

    def test_it_refuses_a_call_naming_that_member(self) -> None:
        assert _refusal(_MEMBER) == "ToolAccessDeniedError"

    def test_it_refuses_a_call_the_group_routes_to_that_member(self) -> None:
        assert _refusal(_GROUP) == "ToolAccessDeniedError"

    def test_it_does_not_govern_the_sibling(self) -> None:
        assert _refusal(_SIBLING) is None


class TestAGroupWithdrawal:
    def test_a_withdrawal_for_every_tenant_refuses_the_member(self, world) -> None:
        get_tool_projection_registry().set_config_withdrawal(_GROUP, _TOOL, tenant_id=None)

        assert _refusal(_GROUP) == "ToolWithdrawnError"
        assert _refusal(_MEMBER) == "ToolWithdrawnError"
        assert _refusal(_SIBLING) == "ToolWithdrawnError"
        assert _refusal(_MEMBER, tenant=None) == "ToolWithdrawnError"
        assert _refusal(_MEMBER, _OPEN_TOOL) is None

    def test_a_withdrawal_for_one_tenant_refuses_that_tenant_on_the_member(self, world) -> None:
        get_tool_projection_registry().set_config_withdrawal(_GROUP, _TOOL, tenant_id=_TENANT)

        assert _refusal(_GROUP) == "ToolWithdrawnError"
        assert _refusal(_MEMBER) == "ToolWithdrawnError"

    def test_a_withdrawal_for_one_tenant_leaves_the_others_alone(self, world) -> None:
        get_tool_projection_registry().set_config_withdrawal(_GROUP, _TOOL, tenant_id=_TENANT)

        assert _refusal(_MEMBER, tenant=_OTHER_TENANT) is None
        assert _refusal(_MEMBER, tenant=None) is None

    def test_a_runtime_withdrawal_on_the_group_refuses_the_member(self, world) -> None:
        """`POST /admin/tools/<group>/<tool>/withdraw` writes the runtime overlay."""
        get_tool_projection_registry().withdraw(_GROUP, _TOOL, tenant_id=_TENANT)

        assert _refusal(_MEMBER) == "ToolWithdrawnError"
        assert _refusal(_MEMBER, tenant=_OTHER_TENANT) is None

    def test_the_refusal_is_published(self, world) -> None:
        get_tool_projection_registry().set_config_withdrawal(_GROUP, _TOOL, tenant_id=None)

        _call(_MEMBER)

        [event] = [
            c.args[0]
            for c in world.context.event_bus.publish.call_args_list
            if isinstance(c.args[0], ToolWithdrawnRejected)
        ]
        assert (event.tenant_id, event.mcp_server, event.tool) == (_TENANT, _MEMBER, _TOOL)


class TestAGroupPin:
    def test_a_stale_pin_on_the_group_refuses_the_member(self, world) -> None:
        get_tool_projection_registry().set_config_pin(_GROUP, _TOOL, None, ToolDigest(_TOOL, _STALE_DIGEST))

        assert _refusal(_GROUP) == "ToolDigestMismatchError"
        assert _refusal(_MEMBER) == "ToolDigestMismatchError"

    def test_a_stale_pin_for_one_tenant_refuses_that_tenant_only(self, world) -> None:
        get_tool_projection_registry().set_config_pin(_GROUP, _TOOL, _TENANT, ToolDigest(_TOOL, _STALE_DIGEST))

        assert _refusal(_MEMBER) == "ToolDigestMismatchError"
        assert _refusal(_MEMBER, tenant=_OTHER_TENANT) is None

    def test_a_matching_pin_on_the_group_lets_the_member_through(self, world) -> None:
        registry = get_tool_projection_registry()
        projection = registry.resolve(_MEMBER, _TOOL)
        assert projection is not None
        registry.set_config_pin(_GROUP, _TOOL, None, projection.digest)

        assert _refusal(_MEMBER) is None

    def test_the_member_must_match_its_own_pin_and_the_groups(self, world) -> None:
        """Deny wins across pins: a matching pin of the member's own does not excuse the group's."""
        registry = get_tool_projection_registry()
        projection = registry.resolve(_MEMBER, _TOOL)
        assert projection is not None
        registry.set_config_pin(_MEMBER, _TOOL, None, projection.digest)
        registry.set_config_pin(_GROUP, _TOOL, None, ToolDigest(_TOOL, _STALE_DIGEST))

        assert _refusal(_MEMBER) == "ToolDigestMismatchError"

    def test_the_group_pin_is_enforced_in_the_groups_mode(self, world) -> None:
        """As a call naming the group enforces it: `warn` on the group reports and lets it run."""
        registry = get_tool_projection_registry()
        registry.set_digest_enforcement(_GROUP, DigestEnforcement.WARN)
        registry.set_config_pin(_GROUP, _TOOL, None, ToolDigest(_TOOL, _STALE_DIGEST))

        assert _refusal(_GROUP) is None
        assert _refusal(_MEMBER) is None


class TestAMemberOfSeveralGroups:
    """Every group that owns the member governs it, and deny wins."""

    @pytest.fixture(autouse=True)
    def _second_owner(self, world) -> None:
        world.groups[_OTHER_GROUP] = _group(_OTHER_GROUP, world.servers[_MEMBER])

    def test_a_deny_on_either_group_refuses_the_member(self) -> None:
        get_tool_access_resolver().set_group_policy(_OTHER_GROUP, ToolAccessPolicy(deny_list=(_TOOL,)))

        assert _refusal(_MEMBER) == "ToolAccessDeniedError"

    def test_a_withdrawal_on_either_group_refuses_the_member(self) -> None:
        get_tool_projection_registry().set_config_withdrawal(_OTHER_GROUP, _TOOL, tenant_id=None)

        assert _refusal(_MEMBER) == "ToolWithdrawnError"

    def test_a_call_naming_a_group_is_governed_by_that_group(self) -> None:
        """Unchanged: naming `pool` asks `pool`, whichever other group its member is in."""
        get_tool_access_resolver().set_group_policy(_OTHER_GROUP, ToolAccessPolicy(deny_list=(_TOOL,)))

        assert _refusal(_GROUP) is None
        assert _refusal(_OTHER_GROUP) == "ToolAccessDeniedError"


class TestADirectCallStaysDirect:
    def test_the_call_goes_to_the_member_it_names(self, world) -> None:
        """The group's first choice is member-a. Naming member-b still reaches member-b."""
        with patch.object(
            world.groups[_GROUP], "select_member_for", wraps=world.groups[_GROUP].select_member_for
        ) as selection:
            assert _refusal(_SIBLING) is None

        selection.assert_not_called()
        assert _dispatched_to(world.context) == [_SIBLING]

    def test_a_server_in_no_group_is_not_governed_by_one(self, world) -> None:
        get_tool_access_resolver().set_group_policy(_GROUP, ToolAccessPolicy(deny_list=(_TOOL,)))
        registry = get_tool_projection_registry()
        registry.set_config_withdrawal(_GROUP, _OPEN_TOOL, tenant_id=None)
        registry.set_config_pin(_GROUP, _TOOL, None, ToolDigest(_TOOL, _STALE_DIGEST))

        assert _refusal(_SOLO) is None
        assert _refusal(_SOLO, _OPEN_TOOL) is None
        assert _dispatched_to(world.context) == [_SOLO, _SOLO]

    def test_a_server_in_no_group_keeps_its_own_policy(self, world) -> None:
        get_tool_access_resolver().set_mcp_server_policy(_SOLO, ToolAccessPolicy(deny_list=(_TOOL,)))

        assert _refusal(_SOLO) == "ToolAccessDeniedError"
        assert _refusal(_SOLO, _OPEN_TOOL) is None

    def test_a_member_keeps_its_own_policy(self, world) -> None:
        """What governed a member before is still applied, on top of its group."""
        get_tool_access_resolver().set_mcp_server_policy(_MEMBER, ToolAccessPolicy(deny_list=(_TOOL,)))

        assert _refusal(_MEMBER) == "ToolAccessDeniedError"
        assert _refusal(_SIBLING) is None


class TestThePostHoldRecheck:
    """The re-check after an approval hold asks the member's groups too (#1039's rule)."""

    def _revalidate(self, *, owning_groups: tuple[str, ...]) -> object:
        return BatchExecutor()._revalidate_after_hold(
            CallSpec(index=0, call_id="c-1", mcp_server=_MEMBER, tool=_TOOL, arguments={}),
            get_tool_access_resolver(),
            Mock(approval_gate=None),
            "approval-1",
            None,
            get_tool_projection_registry(),
            _TENANT,
            lambda _projection, _pin: None,
            target_server_id=_MEMBER,
            owning_groups=owning_groups,
        )

    def test_a_deny_added_to_the_group_during_the_hold_refuses(self) -> None:
        get_tool_access_resolver().set_group_policy(_GROUP, ToolAccessPolicy(deny_list=(_TOOL,)))

        refusal = self._revalidate(owning_groups=(_GROUP,))

        assert refusal is not None
        assert refusal.error_type == "ToolAccessDenied"

    def test_a_call_the_group_still_allows_dispatches(self) -> None:
        get_tool_access_resolver().set_group_policy(_GROUP, ToolAccessPolicy(deny_list=("something_else",)))

        assert self._revalidate(owning_groups=(_GROUP,)) is None


class TestTheHangarCallTool:
    """The registered tool function, with auth off: what an egress caller reaches."""

    @staticmethod
    def _hangar_call(target: str, tool: str = _TOOL) -> dict:
        response = hangar_call(calls=[{"mcp_server": target, "tool": tool, "arguments": {}}], ctx=None)
        [result] = response["results"]
        return result

    @pytest.fixture(autouse=True)
    def _unbound_identity(self, monkeypatch):
        monkeypatch.setattr(batch, "get_context", lambda: SimpleNamespace(auth_components=None), raising=False)
        token = identity_context_var.set(None)
        yield
        identity_context_var.reset(token)

    def test_a_group_deny_refuses_the_member(self, world) -> None:
        get_tool_access_resolver().set_group_policy(_GROUP, ToolAccessPolicy(deny_list=(_TOOL,)))

        assert self._hangar_call(_GROUP)["error_type"] == "ToolAccessDeniedError"
        assert self._hangar_call(_MEMBER)["error_type"] == "ToolAccessDeniedError"
        assert self._hangar_call(_MEMBER, _OPEN_TOOL)["success"] is True

    def test_a_group_withdrawal_refuses_the_member(self, world) -> None:
        get_tool_projection_registry().set_config_withdrawal(_GROUP, _TOOL, tenant_id=None)

        assert self._hangar_call(_GROUP)["error_type"] == "ToolWithdrawnError"
        assert self._hangar_call(_MEMBER)["error_type"] == "ToolWithdrawnError"


# --- the approval gate, and the re-check after its hold -----------------------
#
# The same class of defect: governance on the egress path missing its group or
# tenant scope. `_check_approval_gate` read only the named server's approval
# list, with no tenant, and `_revalidate_after_hold` did not re-check withdrawal.


class _Gate:
    """An approval gate that answers at once, and can change the world while it "holds"."""

    def __init__(self, *, grant: bool, during_hold: Callable[[], None] | None = None) -> None:
        self.asked: list[tuple[str, str, ToolAccessPolicy]] = []
        self._grant = grant
        self._during_hold = during_hold

    async def check(self, **kwargs: Any) -> ApprovalResult:
        self.asked.append((kwargs["mcp_server_id"], kwargs["tool_name"], kwargs["policy"]))
        if self._during_hold is not None:
            self._during_hold()
        return ApprovalResult.granted("approval-1") if self._grant else ApprovalResult.denied("approval-1", reason="no")

    async def revalidate(self, _approval_id: str, _arguments: dict) -> None:
        return None  # the record itself is still valid; the world around it moved


_HELD = "approval_denied"


class TestTheApprovalGateAsksEveryScope:
    """An approval list declared on a group, or for a tenant, holds the call."""

    @pytest.fixture()
    def gate(self, world) -> _Gate:
        world.context.approval_gate = _Gate(grant=False)
        return world.context.approval_gate

    def test_a_group_approval_list_holds_a_call_naming_the_group(self, gate) -> None:
        get_tool_access_resolver().set_group_policy(_GROUP, ToolAccessPolicy(approval_list=(_TOOL,)))

        assert _refusal(_GROUP) == _HELD

    def test_a_group_approval_list_holds_a_call_naming_a_member(self, gate) -> None:
        get_tool_access_resolver().set_group_policy(_GROUP, ToolAccessPolicy(approval_list=(_TOOL,)))

        assert _refusal(_MEMBER) == _HELD
        assert _refusal(_SIBLING) == _HELD

    def test_a_member_approval_list_in_the_group_spec_holds_that_member(self, gate) -> None:
        get_tool_access_resolver().set_member_policy(
            group_id=_GROUP, member_id=_MEMBER, policy=ToolAccessPolicy(approval_list=(_TOOL,)), mcp_server_id=_MEMBER
        )

        assert _refusal(_MEMBER) == _HELD
        assert _refusal(_GROUP) == _HELD  # the group routes to member-a first
        assert _refusal(_SIBLING) is None

    def test_a_tenant_approval_list_holds_that_tenant(self, gate) -> None:
        """`tool_access.member.<tenant>` on a server in no group."""
        get_tool_access_resolver().set_standalone_member_policy(
            _SOLO, _TENANT, ToolAccessPolicy(approval_list=(_TOOL,))
        )

        assert _refusal(_SOLO) == _HELD
        assert _refusal(_SOLO, tenant=_OTHER_TENANT) is None

    def test_a_tenant_approval_list_on_the_group_holds_that_tenant(self, gate) -> None:
        get_tool_access_resolver().set_standalone_member_policy(
            _GROUP, _TENANT, ToolAccessPolicy(approval_list=(_TOOL,))
        )

        assert _refusal(_GROUP) == _HELD
        assert _refusal(_MEMBER) == _HELD
        assert _refusal(_MEMBER, tenant=_OTHER_TENANT) is None

    def test_the_gate_is_handed_the_policy_that_asked(self, gate) -> None:
        """Its timeout and channel are the ones the group declared."""
        get_tool_access_resolver().set_group_policy(
            _GROUP, ToolAccessPolicy(approval_list=(_TOOL,), approval_timeout_seconds=7)
        )

        _call(_MEMBER)

        [(server, tool, policy)] = gate.asked
        assert (server, tool) == (_MEMBER, _TOOL)
        assert policy.requires_approval(_TOOL)
        assert policy.approval_timeout_seconds == 7

    def test_a_server_approval_list_still_holds(self, gate) -> None:
        get_tool_access_resolver().set_mcp_server_policy(_SOLO, ToolAccessPolicy(approval_list=(_TOOL,)))

        assert _refusal(_SOLO) == _HELD
        assert _refusal(_SOLO, _OPEN_TOOL) is None

    def test_nothing_is_held_without_an_approval_list(self, gate) -> None:
        for target in (_GROUP, _MEMBER, _SOLO):
            assert _refusal(target) is None
        assert gate.asked == []

    def test_in_front_door_the_callers_tenant_is_asked(self, gate) -> None:
        """Asked without it, the resolver answered deny-all, which asks for no approval (#1039)."""
        resolver = get_tool_access_resolver()
        resolver.set_topology_mode("front_door")
        resolver.set_mcp_server_policy(_SOLO, ToolAccessPolicy(approval_list=(_TOOL,)))

        assert _refusal(_SOLO) == _HELD
        assert _refusal(_SOLO, _OPEN_TOOL) is None

    def test_in_front_door_a_caller_with_no_tenant_is_still_refused_before_the_gate(self, gate) -> None:
        get_tool_access_resolver().set_topology_mode("front_door")

        assert _refusal(_SOLO, tenant=None) == "ToolAccessDeniedError"
        assert gate.asked == []


class TestAWithdrawalDuringTheHold:
    """A tool withdrawn while the call waits for a human does not run once approved."""

    @staticmethod
    def _approved_after(world, withdraw: Callable[[], None]) -> None:
        world.context.approval_gate = _Gate(grant=True, during_hold=withdraw)

    @staticmethod
    def _needs_approval(server_id: str) -> None:
        """On the server's own policy, which the approval gate has always read."""
        get_tool_access_resolver().set_mcp_server_policy(server_id, ToolAccessPolicy(approval_list=(_TOOL,)))

    def test_withdrawn_on_the_server(self, world) -> None:
        self._needs_approval(_SOLO)
        self._approved_after(world, lambda: get_tool_projection_registry().withdraw(_SOLO, _TOOL))

        assert _refusal(_SOLO) == "ToolWithdrawnError"
        assert _dispatched_to(world.context) == []

    def test_withdrawn_on_the_group_a_call_names(self, world) -> None:
        self._needs_approval(_GROUP)
        self._approved_after(world, lambda: get_tool_projection_registry().withdraw(_GROUP, _TOOL))

        assert _refusal(_GROUP) == "ToolWithdrawnError"
        assert _dispatched_to(world.context) == []

    def test_withdrawn_on_a_group_that_owns_the_member_a_call_names(self, world) -> None:
        self._needs_approval(_MEMBER)
        self._approved_after(world, lambda: get_tool_projection_registry().withdraw(_GROUP, _TOOL))

        assert _refusal(_MEMBER) == "ToolWithdrawnError"
        assert _dispatched_to(world.context) == []

    def test_withdrawn_by_a_config_reload_on_an_owning_group(self, world) -> None:
        self._needs_approval(_MEMBER)
        self._approved_after(
            world, lambda: get_tool_projection_registry().set_config_withdrawal(_GROUP, _TOOL, tenant_id=None)
        )

        assert _refusal(_MEMBER) == "ToolWithdrawnError"
        assert _dispatched_to(world.context) == []

    def test_withdrawn_for_the_callers_tenant(self, world) -> None:
        self._needs_approval(_SOLO)
        self._approved_after(world, lambda: get_tool_projection_registry().withdraw(_SOLO, _TOOL, tenant_id=_TENANT))

        assert _refusal(_SOLO) == "ToolWithdrawnError"
        assert _dispatched_to(world.context) == []

    def test_withdrawn_for_another_tenant_it_still_runs(self, world) -> None:
        self._needs_approval(_SOLO)
        self._approved_after(
            world, lambda: get_tool_projection_registry().withdraw(_SOLO, _TOOL, tenant_id=_OTHER_TENANT)
        )

        assert _refusal(_SOLO) is None
        assert _dispatched_to(world.context) == [_SOLO]

    def test_the_refusal_is_published(self, world) -> None:
        self._needs_approval(_SOLO)
        self._approved_after(world, lambda: get_tool_projection_registry().withdraw(_SOLO, _TOOL))

        _call(_SOLO)

        [event] = [
            c.args[0]
            for c in world.context.event_bus.publish.call_args_list
            if isinstance(c.args[0], ToolWithdrawnRejected)
        ]
        assert (event.tenant_id, event.mcp_server, event.tool) == (_TENANT, _SOLO, _TOOL)

    def test_an_approved_call_nothing_withdrew_runs(self, world) -> None:
        self._needs_approval(_SOLO)
        self._approved_after(world, lambda: None)

        assert _refusal(_SOLO) is None
        assert _dispatched_to(world.context) == [_SOLO]
