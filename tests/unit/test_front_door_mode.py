"""Unit tests for front_door topology mode (issue #236).

Covers:
- front_door mode + member_id=None  → DENY (fail-closed for unauthenticated callers).
- egress mode   + member_id=None    → server-level policy (backward compat, no regression).
- Unset mode                        → defaults to egress (never silently fails open).
- front_door mode does NOT break the #229 member-scope path (caller WITH tenant resolves normally).
- Executor-level: identity_context_var=None under front_door → call blocked (ToolAccessDeniedError).
- Confirm _DENY_ALL_POLICY.is_tool_allowed() returns False for arbitrary tool names.
"""

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.services.tool_access_resolver import (
    reset_tool_access_resolver,
    ToolAccessResolver,
    _DENY_ALL_POLICY,
)
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def resolver():
    """Fresh ToolAccessResolver (not the global singleton)."""
    r = ToolAccessResolver()
    yield r
    r.clear_all()


@pytest.fixture(autouse=True)
def reset_global_resolver():
    """Reset global resolver singleton before and after each test."""
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


@pytest.fixture()
def mock_context():
    """Minimal application context mock required by BatchExecutor."""
    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()
    ctx.command_bus.send.return_value = {"ok": True}
    ctx.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"),
        has_tools=False,
        health=Mock(should_degrade=Mock(return_value=False)),
    )
    ctx.mcp_server_exists.return_value = True

    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as val_groups,
    ):
        exec_groups.get.return_value = None
        val_groups.get.return_value = None
        yield ctx


# ---------------------------------------------------------------------------
# _DENY_ALL_POLICY semantics
# ---------------------------------------------------------------------------


class TestDenyAllPolicy:
    """Verify the sentinel policy denies every tool name."""

    def test_deny_all_blocks_arbitrary_tool(self):
        assert not _DENY_ALL_POLICY.is_tool_allowed("read_file")
        assert not _DENY_ALL_POLICY.is_tool_allowed("list_buckets")
        assert not _DENY_ALL_POLICY.is_tool_allowed("anything")

    def test_deny_all_is_not_unrestricted(self):
        assert not _DENY_ALL_POLICY.is_unrestricted()


# ---------------------------------------------------------------------------
# Resolver-level: topology mode
# ---------------------------------------------------------------------------


class TestTopologyModeResolver:
    """Resolver._compute_effective_policy respects topology mode."""

    def test_default_mode_is_egress(self, resolver):
        """Unset mode defaults to egress — backward compat, no regression."""
        resolver.set_mcp_server_policy("srv", ToolAccessPolicy(deny_list=("bad_tool",)))
        policy = resolver.resolve_effective_policy("srv")
        # Egress: server policy returned; bad_tool blocked, good_tool allowed.
        assert not policy.is_tool_allowed("bad_tool")
        assert policy.is_tool_allowed("good_tool")

    def test_egress_mode_no_member_returns_server_policy(self, resolver):
        """egress + member_id=None → server-level policy."""
        resolver.set_topology_mode("egress")
        resolver.set_mcp_server_policy("srv", ToolAccessPolicy(allow_list=("safe_tool",)))
        policy = resolver.resolve_effective_policy("srv")
        assert policy.is_tool_allowed("safe_tool")
        assert not policy.is_tool_allowed("other_tool")

    def test_front_door_mode_no_member_denies_all(self, resolver):
        """front_door + member_id=None → DENY regardless of server policy."""
        resolver.set_topology_mode("front_door")
        # Even with a permissive server policy, unauthenticated caller is denied.
        resolver.set_mcp_server_policy("srv", ToolAccessPolicy(allow_list=("safe_tool",)))
        policy = resolver.resolve_effective_policy("srv")
        assert not policy.is_tool_allowed("safe_tool")
        assert not policy.is_tool_allowed("anything")

    def test_front_door_group_call_without_member_is_denied(self, resolver):
        """front_door + group target + no tenant → DENY (the group path is not a bypass)."""
        resolver.set_topology_mode("front_door")
        resolver.set_mcp_server_policy("grpsrv", ToolAccessPolicy())  # permissive
        # Unauthenticated caller targeting a GROUP (group_id present, member_id None).
        policy = resolver.resolve_effective_policy("grpsrv", group_id="grpsrv", member_id=None)
        assert not policy.is_tool_allowed("any_tool")

    def test_egress_group_call_without_member_not_denied_by_mode(self, resolver):
        """egress + group target + no tenant → mode does not deny (regression guard for the fix)."""
        resolver.set_topology_mode("egress")
        resolver.set_mcp_server_policy("grpsrv", ToolAccessPolicy())
        policy = resolver.resolve_effective_policy("grpsrv", group_id="grpsrv", member_id=None)
        assert policy.is_tool_allowed("any_tool")

    def test_opposite_defaults_for_same_unauthenticated_call(self, resolver):
        """egress and front_door produce opposite results for the same no-member call."""
        resolver.set_mcp_server_policy("srv", ToolAccessPolicy())

        resolver.set_topology_mode("egress")
        egress_policy = resolver.resolve_effective_policy("srv")

        # Invalidate cache before switching mode
        resolver.invalidate_cache("srv")
        resolver.set_topology_mode("front_door")
        fd_policy = resolver.resolve_effective_policy("srv")

        assert egress_policy.is_tool_allowed("any_tool")
        assert not fd_policy.is_tool_allowed("any_tool")

    def test_front_door_mode_with_member_id_resolves_member_policy(self, resolver):
        """front_door mode does NOT break the #229 member-scope path.

        A caller WITH a tenant still resolves the server→member merge correctly.
        """
        resolver.set_topology_mode("front_door")
        resolver.set_mcp_server_policy("srv", ToolAccessPolicy(allow_list=("read", "write")))
        resolver.set_standalone_member_policy(
            "srv", "tenant:alice", ToolAccessPolicy(deny_list=("write",))
        )

        # Authenticated caller: member merge applies, NOT deny-all.
        assert resolver.is_tool_allowed("srv", "read", member_id="tenant:alice")
        assert not resolver.is_tool_allowed("srv", "write", member_id="tenant:alice")

    def test_set_topology_mode_invalidates_no_member_cache(self, resolver):
        """Switching topology mode flushes the no-member cache entries."""
        resolver.set_topology_mode("egress")
        # Populate cache
        resolver.resolve_effective_policy("srv")
        with resolver._lock:
            assert "mcp_server:srv" in resolver._policy_cache

        # Switch to front_door — cache must be cleared
        resolver.set_topology_mode("front_door")
        with resolver._lock:
            assert "mcp_server:srv" not in resolver._policy_cache

    def test_clear_all_resets_topology_mode_to_egress(self, resolver):
        """clear_all() must reset topology mode to the safe default (egress)."""
        resolver.set_topology_mode("front_door")
        resolver.clear_all()
        # After clear, unauthenticated caller gets server policy (egress default).
        resolver.set_mcp_server_policy("srv", ToolAccessPolicy())
        policy = resolver.resolve_effective_policy("srv")
        assert policy.is_tool_allowed("any_tool")


# ---------------------------------------------------------------------------
# Executor-level: identity_context_var absent under front_door
# ---------------------------------------------------------------------------


class TestFrontDoorModeExecutor:
    """BatchExecutor blocks unauthenticated callers in front_door mode."""

    def _make_identity(self, tenant_id: str | None) -> IdentityContext:
        caller = CallerIdentity(
            user_id=None,
            agent_id=None,
            session_id=None,
            principal_type="anonymous",
            tenant_id=tenant_id,
        )
        return IdentityContext(caller=caller)

    def test_front_door_no_identity_blocks_call(self, mock_context):
        """front_door + no identity context → ToolAccessDeniedError, command_bus not called."""
        from mcp_hangar.domain.services import get_tool_access_resolver

        resolver = get_tool_access_resolver()
        resolver.set_topology_mode("front_door")

        token = identity_context_var.set(None)
        try:
            executor = BatchExecutor()
            calls = [
                CallSpec(
                    index=0,
                    call_id="fd-no-id-0",
                    mcp_server="myserver",
                    tool="any_tool",
                    arguments={},
                )
            ]
            result = executor.execute(
                batch_id="fd-no-id-batch",
                calls=calls,
                max_concurrency=1,
                global_timeout=30.0,
                fail_fast=False,
            )
        finally:
            identity_context_var.reset(token)

        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolAccessDeniedError"
        mock_context.command_bus.send.assert_not_called()

    def test_egress_no_identity_uses_server_policy(self, mock_context):
        """egress + no identity context → server policy applied (no regression)."""
        from mcp_hangar.domain.services import get_tool_access_resolver

        resolver = get_tool_access_resolver()
        resolver.set_topology_mode("egress")

        token = identity_context_var.set(None)
        try:
            executor = BatchExecutor()
            calls = [
                CallSpec(
                    index=0,
                    call_id="egress-no-id-0",
                    mcp_server="myserver",
                    tool="safe_tool",
                    arguments={},
                )
            ]
            result = executor.execute(
                batch_id="egress-no-id-batch",
                calls=calls,
                max_concurrency=1,
                global_timeout=30.0,
                fail_fast=False,
            )
        finally:
            identity_context_var.reset(token)

        # Server policy is unrestricted → tool is allowed
        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()

    def test_front_door_authenticated_caller_allowed(self, mock_context):
        """front_door + caller WITH tenant → tool resolved by member policy, not deny-all."""
        from mcp_hangar.domain.services import get_tool_access_resolver

        resolver = get_tool_access_resolver()
        resolver.set_topology_mode("front_door")
        # No server-level deny; tenant:alice has no restrictions.

        identity_ctx = self._make_identity("tenant:alice")
        token = identity_context_var.set(identity_ctx)
        try:
            executor = BatchExecutor()
            calls = [
                CallSpec(
                    index=0,
                    call_id="fd-auth-0",
                    mcp_server="myserver",
                    tool="read_item",
                    arguments={},
                )
            ]
            result = executor.execute(
                batch_id="fd-auth-batch",
                calls=calls,
                max_concurrency=1,
                global_timeout=30.0,
                fail_fast=False,
            )
        finally:
            identity_context_var.reset(token)

        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()
