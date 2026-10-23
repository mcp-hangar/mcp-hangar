"""`hangar_tools` and `hangar_details` decide each name through `decide()` (#1784, D1 to D3).

PR 4 of the plan on #1763. `hangar_tools` on a group used to select a member
without the tenant, advancing the strategy and starting that member from a
listing, and then filter its catalogue with the member's server id in the
tenant slot (D1). A server named directly was decided under its own scope
alone, so a tool a group denied on its member stayed listed and was refused
when called (D2), and no listing read the withdrawal overlay (D3). Now every
name is decided as `hangar_call` decides it, on the members a call for this
tenant may be routed to, and a listing selects and starts nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.mcp_server_views import ToolInfo
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import CanaryPolicy, McpServerGroup
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.server.tools import mcp_server as tools

GROUP, A, B, SOLO = "pool", "member-a", "member-b", "solo"
PINNED, OTHER = "tenant:pinned", "tenant:other"
CATALOGUE = ("query_rows", "admin_reset")


@pytest.fixture(autouse=True)
def _clean() -> Any:
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    yield
    reset_tool_access_resolver()
    reset_tool_projection_registry()


@contextmanager
def _as(tenant_id: str | None) -> Iterator[None]:
    caller = CallerIdentity(
        user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
    )
    token = identity_context_var.set(IdentityContext(caller=caller) if tenant_id is not None else None)
    try:
        yield
    finally:
        identity_context_var.reset(token)


def _server(server_id: str) -> McpServer:
    return McpServer(mcp_server_id=server_id, mode="subprocess", command=["unused"], metrics_publisher=Mock())


def _pool(*in_rotation: str, pinned: dict[str, str] | None = None) -> McpServerGroup:
    """`member-a` and `member-b`, never started; *in_rotation* names the routable ones."""
    group = McpServerGroup(GROUP, auto_start=False)
    for server_id in (A, B):
        group.add_member(_server(server_id))
        group.get_member(server_id).in_rotation = server_id in in_rotation  # type: ignore[union-attr]
    if pinned:
        group.set_canary_policy(CanaryPolicy(pinned_tenants=pinned))
    return group


def _deny_admin_on(member: str) -> None:
    """The group's policy on *member*, keyed by its server id as a call keys it."""
    get_tool_access_resolver().set_member_policy(
        GROUP, member, ToolAccessPolicy(deny_list=("admin_*",)), mcp_server_id=member
    )


def _catalogue() -> list[ToolInfo]:
    return [ToolInfo(name=name, description="", input_schema={}) for name in CATALOGUE]


def _topology(group: McpServerGroup | None) -> Any:
    groups = {} if group is None else {GROUP: group}
    return patch.dict("mcp_hangar.server.bootstrap.composition.GROUPS", groups, clear=True)


def _group_ctx(group: McpServerGroup) -> SimpleNamespace:
    return SimpleNamespace(
        get_group=lambda _id: group,
        query_bus=SimpleNamespace(execute=lambda _query: _catalogue()),
        command_bus=SimpleNamespace(send=Mock(side_effect=AssertionError("a group listing starts nothing"))),
    )


def _server_ctx() -> SimpleNamespace:
    declared = SimpleNamespace(
        has_tools=True,
        tools=SimpleNamespace(list_tools=_catalogue),
        state=SimpleNamespace(value="ready"),
        tools_predefined=True,
    )
    return SimpleNamespace(get_mcp_server=lambda _id: declared)


def _group_listing(group: McpServerGroup, tenant_id: str | None) -> set[str]:
    with _as(tenant_id), _topology(group), patch.object(tools, "get_context", return_value=_group_ctx(group)):
        return {tool["name"] for tool in tools._get_tools_for_group(GROUP)["tools"]}


def _server_listing(server_id: str, tenant_id: str | None, group: McpServerGroup | None) -> set[str]:
    with _as(tenant_id), _topology(group), patch.object(tools, "get_context", return_value=_server_ctx()):
        return {tool["name"] for tool in tools._get_tools_for_mcp_server(server_id)["tools"]}


def _details(server_id: str, tenant_id: str | None, group: McpServerGroup | None) -> dict[str, Any]:
    result = {"tools": [{"name": name, "description": "", "inputSchema": {}} for name in CATALOGUE], "tools_policy": {}}
    with _as(tenant_id), _topology(group):
        return tools._govern_details(server_id, result)


class TestD1AGroupListing:
    """Decided on every member a call for the tenant may be routed to; selects and starts none."""

    @pytest.fixture(autouse=True)
    def _member_b_denies_admin(self) -> None:
        _deny_admin_on(B)

    def test_a_tool_one_member_denies_is_hidden_for_a_load_balanced_tenant(self) -> None:
        assert _group_listing(_pool(A, B), OTHER) == {"query_rows"}

    def test_a_tenant_pinned_to_the_member_that_allows_it_sees_it(self) -> None:
        group = _pool(A, B, pinned={PINNED: A})

        assert _group_listing(group, PINNED) == set(CATALOGUE)
        assert _group_listing(group, OTHER) == {"query_rows"}

    def test_it_is_listed_once_the_denying_member_leaves_rotation(self) -> None:
        assert _group_listing(_pool(A), OTHER) == set(CATALOGUE)

    def test_no_member_in_rotation_lists_nothing_and_raises_no_more(self) -> None:
        group = _pool()
        with _as(OTHER), _topology(group), patch.object(tools, "get_context", return_value=_group_ctx(group)):
            assert tools._get_tools_for_group(GROUP) == {"mcp_server": GROUP, "group": True, "tools": []}

    def test_it_selects_nothing_and_starts_nothing(self) -> None:
        """The strategy's clock does not move, and no member is sent a start; a selection would do both."""
        group = _pool(A, B)
        ctx = _group_ctx(group)
        clocks = [group.get_member(m).last_selected_at for m in (A, B)]  # type: ignore[union-attr]

        with _as(OTHER), _topology(group), patch.object(tools, "get_context", return_value=ctx):
            tools._get_tools_for_group(GROUP)

        assert [group.get_member(m).last_selected_at for m in (A, B)] == clocks  # type: ignore[union-attr]
        ctx.command_bus.send.assert_not_called()


class TestD2AServerNamedDirectly:
    """Decided under every group that owns it too, as `hangar_call` naming it is."""

    def test_a_group_policy_on_the_member_hides_the_tool_from_hangar_tools(self) -> None:
        _deny_admin_on(A)

        assert _server_listing(A, OTHER, _pool(A, B)) == {"query_rows"}
        assert _server_listing(A, OTHER, None) == set(CATALOGUE), "the policy is the group's, not the server's"

    def test_a_group_policy_on_the_member_hides_the_tool_from_hangar_details(self) -> None:
        _deny_admin_on(A)

        result = _details(A, OTHER, _pool(A, B))

        assert [tool["name"] for tool in result["tools"]] == ["query_rows"]
        assert result["tools_policy"] == {"filtered_count": 1}
        assert "withheld_tools" not in result

    def test_a_tenant_policy_on_the_server_still_applies(self) -> None:
        get_tool_access_resolver().set_standalone_member_policy(SOLO, OTHER, ToolAccessPolicy(deny_list=("admin_*",)))

        assert _server_listing(SOLO, OTHER, None) == {"query_rows"}
        assert _server_listing(SOLO, PINNED, None) == set(CATALOGUE)


class TestD3Withdrawal:
    """A withdrawn tool is refused on call, so it is not listed either."""

    def test_a_withdrawal_hides_the_tool_from_hangar_tools_and_hangar_details(self) -> None:
        get_tool_projection_registry().withdraw(SOLO, "admin_reset", None)

        assert _server_listing(SOLO, OTHER, None) == {"query_rows"}
        assert [tool["name"] for tool in _details(SOLO, OTHER, None)["tools"]] == ["query_rows"]

    def test_a_withdrawal_for_one_tenant_hides_it_for_that_tenant_alone(self) -> None:
        get_tool_projection_registry().withdraw(SOLO, "admin_reset", OTHER)

        assert _server_listing(SOLO, OTHER, None) == {"query_rows"}
        assert _server_listing(SOLO, PINNED, None) == set(CATALOGUE)

    def test_a_withdrawal_on_a_candidate_hides_the_name_from_the_group_listing(self) -> None:
        get_tool_projection_registry().withdraw(B, "query_rows", None)

        assert _group_listing(_pool(A, B), OTHER) == {"admin_reset"}
        assert _group_listing(_pool(A), OTHER) == set(CATALOGUE)
