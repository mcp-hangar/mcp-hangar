"""A group is not a way past the withdrawal gate or the digest pin (#1040, #1039).

`call.mcp_server` is the GROUP id whenever a group is the target: front_door
collapses the member deliberately so selection stays with the group's strategy
(#857), and an egress caller names the group directly. The projection registry is
keyed by the id that STARTED, which is always a member -- so `resolve(group_id,
tool)` returned `None`, and `None` means "unknown tool, do not block". The
withdrawal gate waved every group-routed call through and the pin gate returned
before checking anything, in both topologies and with no listing filter behind
the pin.

The post-approval re-check had the mirror-image omission (#1039): it re-resolved
the policy with neither the group nor the caller's tenant, although both were in
scope one frame up. In front_door that is the fail-closed missing-identity branch,
so every human-approved call was refused at dispatch; in egress a deny added to
the group during the hold -- the exact race the re-check exists to close -- was
not seen.

Both are driven here through `BatchExecutor.execute`, not by asserting on the
registry: the registry answered correctly all along, and the id it was asked
about was the defect.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec

_GROUP = "group_g"
_MEMBER = "member_1"
_TOOL = "read_item"
_TENANT = "tenant:A"
_STALE_DIGEST = "a" * 64  # never matches the real schema digest


def _identity(tenant_id: str | None) -> IdentityContext:
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


@pytest.fixture()
def ctx():
    """A healthy group whose selected member is `_MEMBER`, every gate open."""
    member = Mock(
        state=Mock(value="ready"),
        has_tools=False,
        health=Mock(should_degrade=Mock(return_value=False)),
    )
    member.id.value = _MEMBER

    group = Mock()
    group.select_member_for.return_value = member
    group.select_member.return_value = member

    context = Mock()
    context.event_bus = Mock()
    context.command_bus = Mock()
    context.command_bus.send.return_value = {"ok": True}
    context.governed_task_store = None
    context.approval_gate = None
    # A group id is not a server id: this is what makes `_gate_resolve_target`
    # take the group branch and select a member, exactly as in production.
    context.get_mcp_server.side_effect = lambda server_id: None if server_id == _GROUP else member
    context.mcp_server_exists.return_value = True
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=context),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=context),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS", {_GROUP: group}),
        patch("mcp_hangar.server.tools.batch.validator.GROUPS", {_GROUP: group}),
    ):
        yield context


def _call_the_group():
    token = identity_context_var.set(_identity(_TENANT))
    try:
        batch = BatchExecutor().execute(
            batch_id="b",
            calls=[CallSpec(index=0, call_id="c-1", mcp_server=_GROUP, tool=_TOOL, arguments={})],
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )
    finally:
        identity_context_var.reset(token)
    return batch.results[0]


def _discover_on_the_member(**kwargs) -> None:
    get_tool_projection_registry().build_from_tools(
        _MEMBER, [ToolSchema(name=_TOOL, description="t", input_schema={})], **kwargs
    )


class TestTheWithdrawalGateSeesAGroupRoutedCall:
    def test_a_tool_withdrawn_on_the_selected_member_is_refused(self, ctx) -> None:
        _discover_on_the_member(tenant_overrides={_TOOL: {_TENANT: "withdrawn"}})

        assert _call_the_group().error_type == "ToolWithdrawnError"

    def test_a_tool_withdrawn_on_the_group_itself_is_refused(self, ctx) -> None:
        """The id a group can now declare under (#1038)."""
        _discover_on_the_member()
        get_tool_projection_registry().set_config_withdrawal(_GROUP, _TOOL, tenant_id=None)

        assert _call_the_group().error_type == "ToolWithdrawnError"

    def test_an_active_tool_still_runs(self, ctx) -> None:
        _discover_on_the_member()

        assert _call_the_group().success is True

    def test_another_tenants_withdrawal_does_not_refuse_this_caller(self, ctx) -> None:
        _discover_on_the_member(tenant_overrides={_TOOL: {"tenant:B": "withdrawn"}})

        assert _call_the_group().success is True


class TestTheDigestPinSurvivesGroupRouting:
    def test_a_stale_pin_on_the_member_refuses_the_call(self, ctx) -> None:
        _discover_on_the_member()
        get_tool_projection_registry().set_config_pin(
            _MEMBER, _TOOL, _TENANT, ToolDigest(tool_name=_TOOL, sha256=_STALE_DIGEST)
        )

        assert _call_the_group().error_type == "ToolDigestMismatchError"

    def test_a_stale_pin_on_the_group_refuses_the_call(self, ctx) -> None:
        _discover_on_the_member()
        get_tool_projection_registry().set_config_pin(
            _GROUP, _TOOL, _TENANT, ToolDigest(tool_name=_TOOL, sha256=_STALE_DIGEST)
        )

        assert _call_the_group().error_type == "ToolDigestMismatchError"

    def test_a_matching_pin_lets_the_call_through(self, ctx) -> None:
        _discover_on_the_member()
        projection = get_tool_projection_registry().resolve(_MEMBER, _TOOL)
        assert projection is not None
        get_tool_projection_registry().set_config_pin(_MEMBER, _TOOL, _TENANT, projection.digest)

        assert _call_the_group().success is True

    def test_an_unpinned_tool_is_unaffected(self, ctx) -> None:
        _discover_on_the_member()

        assert _call_the_group().success is True


class TestThePostHoldRecheckAsksTheSameQuestionTheGateDid:
    """#1039, against the REAL resolver -- a Mock cannot observe either branch."""

    def _revalidate(self, *, mode: str, target: str, group_id: str | None, deny: tuple[str, ...] = ()) -> object:
        resolver = get_tool_access_resolver()
        resolver.set_topology_mode(mode)  # type: ignore[arg-type]
        if deny:
            resolver.set_group_policy(_GROUP, ToolAccessPolicy(deny_list=deny))
        gate = Mock()
        gate.revalidate = None  # no record re-check; the policy branch is under test
        context = Mock()
        context.approval_gate = None
        return BatchExecutor()._revalidate_after_hold(
            CallSpec(index=0, call_id="c-1", mcp_server=target, tool=_TOOL, arguments={}),
            resolver,
            context,
            "approval-1",
            None,
            get_tool_projection_registry(),
            _TENANT,
            lambda projection, pin: None,
            group_id=group_id,
            target_server_id=_MEMBER,
        )

    def test_an_approved_call_dispatches_in_front_door(self) -> None:
        """It used to refuse every one of them: no member_id meant deny-all."""
        assert self._revalidate(mode="front_door", target="payments", group_id=None) is None

    @pytest.mark.parametrize("mode", ["egress", "front_door"])
    def test_a_deny_added_to_the_group_during_the_hold_refuses(self, mode: str) -> None:
        refusal = self._revalidate(mode=mode, target=_GROUP, group_id=_GROUP, deny=(_TOOL,))

        assert refusal is not None
        assert refusal.error_type == "ToolAccessDenied"

    @pytest.mark.parametrize("mode", ["egress", "front_door"])
    def test_a_group_call_the_policy_still_allows_dispatches(self, mode: str) -> None:
        assert self._revalidate(mode=mode, target=_GROUP, group_id=_GROUP, deny=("something_else",)) is None

    def test_a_pin_is_re_verified_against_the_selected_member(self) -> None:
        """The pin re-check has the same two-name problem as the gate (#1040).

        Resolved under the group id alone the projection is `None`, and `None`
        skips the re-verification entirely -- so a pin that stopped matching
        during the hold was not noticed at dispatch.
        """
        get_tool_projection_registry().build_from_tools(
            _MEMBER, [ToolSchema(name=_TOOL, description="t", input_schema={})]
        )
        seen: list[str] = []
        refusal = BatchExecutor()._revalidate_after_hold(
            CallSpec(index=0, call_id="c-1", mcp_server=_GROUP, tool=_TOOL, arguments={}),
            get_tool_access_resolver(),
            Mock(approval_gate=None),
            "approval-1",
            ToolDigest(tool_name=_TOOL, sha256=_STALE_DIGEST),
            get_tool_projection_registry(),
            _TENANT,
            lambda projection, _pin: seen.append(projection.mcp_server) or None,
            group_id=_GROUP,
            target_server_id=_MEMBER,
        )

        assert refusal is None
        assert seen == [_MEMBER], "the member's projection is the schema the pin is checked against"

    def test_a_pin_on_a_tool_no_catalogue_knows_re_verifies_nothing(self) -> None:
        """Both ids answer `None`: nothing to check against, and no crash."""
        called: list[object] = []
        refusal = BatchExecutor()._revalidate_after_hold(
            CallSpec(index=0, call_id="c-1", mcp_server=_GROUP, tool="unknown_tool", arguments={}),
            get_tool_access_resolver(),
            Mock(approval_gate=None),
            "approval-1",
            ToolDigest(tool_name="unknown_tool", sha256=_STALE_DIGEST),
            get_tool_projection_registry(),
            _TENANT,
            lambda projection, _pin: called.append(projection) or None,
            group_id=_GROUP,
            target_server_id=_MEMBER,
        )

        assert refusal is None
        assert called == []
