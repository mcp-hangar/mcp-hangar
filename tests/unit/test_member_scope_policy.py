"""Unit tests for member-scope (tenant) tool access policy enforcement.

Covers:
- Resolver-level: server→member merge (standalone tenant policy).
- Executor-level: caller tenant_id from identity_context_var gates tool access.
- Backward compat: tenant_id None → server-level resolution unchanged.
- Security: member policy cannot re-add a server-denied tool.
"""

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.services.tool_access_resolver import (
    reset_tool_access_resolver,
    ToolAccessResolver,
)
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def resolver():
    """Fresh ToolAccessResolver for each test."""
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
# Resolver-level tests
# ---------------------------------------------------------------------------


class TestStandaloneMemberPolicyResolver:
    """Server→member merge at the resolver level."""

    def test_member_deny_list_blocks_server_allowed_tool(self, resolver):
        """A tenant deny_list blocks a tool that the server-level policy allows."""
        server_policy = ToolAccessPolicy(allow_list=("get_data", "list_data", "delete_data"))
        resolver.set_mcp_server_policy("myserver", server_policy)

        member_policy = ToolAccessPolicy(deny_list=("delete_data",))
        resolver.set_standalone_member_policy("myserver", "tenant:a", member_policy)

        # Without member context: server policy allows delete_data
        assert resolver.is_tool_allowed("myserver", "delete_data")

        # With tenant context: member deny overrides
        assert not resolver.is_tool_allowed("myserver", "delete_data", member_id="tenant:a")
        assert resolver.is_tool_allowed("myserver", "get_data", member_id="tenant:a")

    def test_different_tenant_still_allowed(self, resolver):
        """A different tenant with no member policy sees the server policy only."""
        server_policy = ToolAccessPolicy(allow_list=("get_data", "delete_data"))
        resolver.set_mcp_server_policy("myserver", server_policy)

        member_policy = ToolAccessPolicy(deny_list=("delete_data",))
        resolver.set_standalone_member_policy("myserver", "tenant:a", member_policy)

        # tenant:b has no member policy → falls back to server policy
        assert resolver.is_tool_allowed("myserver", "delete_data", member_id="tenant:b")

    def test_member_id_none_returns_server_policy(self, resolver):
        """member_id=None → server-level resolution (backward compat)."""
        server_policy = ToolAccessPolicy(deny_list=("dangerous_tool",))
        resolver.set_mcp_server_policy("myserver", server_policy)

        member_policy = ToolAccessPolicy(deny_list=("safe_tool",))
        resolver.set_standalone_member_policy("myserver", "tenant:a", member_policy)

        # No member context — only server policy applies
        policy = resolver.resolve_effective_policy("myserver")
        assert not policy.is_tool_allowed("dangerous_tool")  # server deny
        assert policy.is_tool_allowed("safe_tool")  # NOT in server deny

    def test_member_policy_cannot_readd_server_denied_tool(self, resolver):
        """Merge semantics: member policy cannot re-add a server-denied tool."""
        # Server denies dangerous_tool
        server_policy = ToolAccessPolicy(deny_list=("dangerous_tool",))
        resolver.set_mcp_server_policy("myserver", server_policy)

        # Member allows everything (unrestricted allow → can only narrow, not expand)
        # We test that even with an empty (unrestricted) member policy the server deny holds.
        resolver.set_standalone_member_policy("myserver", "tenant:attacker", ToolAccessPolicy())

        assert not resolver.is_tool_allowed("myserver", "dangerous_tool", member_id="tenant:attacker")

    def test_member_policy_cache_key_is_distinct_from_server_cache(self, resolver):
        """Cache key for server→member path is distinct from plain server key."""
        server_policy = ToolAccessPolicy(deny_list=("del",))
        resolver.set_mcp_server_policy("srv", server_policy)

        member_policy = ToolAccessPolicy(deny_list=("get",))
        resolver.set_standalone_member_policy("srv", "tenant:t1", member_policy)

        # Populate both cache entries
        resolver.resolve_effective_policy("srv")
        resolver.resolve_effective_policy("srv", member_id="tenant:t1")

        with resolver._lock:
            assert "mcp_server:srv" in resolver._policy_cache
            assert "mcp_server:srv:member:tenant:t1" in resolver._policy_cache

    def test_set_unrestricted_standalone_member_policy_removes_it(self, resolver):
        """Setting an unrestricted member policy clears it from the store."""
        resolver.set_standalone_member_policy("srv", "tenant:t1", ToolAccessPolicy(deny_list=("x",)))
        resolver.set_standalone_member_policy("srv", "tenant:t1", ToolAccessPolicy())

        with resolver._lock:
            assert ("srv", "tenant:t1") not in resolver._standalone_member_policies

    def test_clear_all_removes_standalone_member_policies(self, resolver):
        """clear_all() must also wipe standalone member policies."""
        resolver.set_standalone_member_policy("srv", "tenant:t1", ToolAccessPolicy(deny_list=("x",)))
        resolver.clear_all()

        with resolver._lock:
            assert len(resolver._standalone_member_policies) == 0


# ---------------------------------------------------------------------------
# Executor-level tests (identity_context_var → member_id)
# ---------------------------------------------------------------------------


class TestMemberScopePolicyExecutor:
    """BatchExecutor reads caller tenant_id and applies member-scope policy."""

    def _make_identity(self, tenant_id: str | None) -> IdentityContext:
        caller = CallerIdentity(
            user_id=None,
            agent_id=None,
            session_id=None,
            principal_type="anonymous",
            tenant_id=tenant_id,
        )
        return IdentityContext(caller=caller)

    def test_tenant_deny_list_blocks_call(self, mock_context):
        """A tenant deny_list prevents the call from reaching command_bus.send."""
        from mcp_hangar.domain.services import get_tool_access_resolver

        resolver = get_tool_access_resolver()
        # Server allows everything; tenant:blocked is denied delete_item
        resolver.set_standalone_member_policy(
            "myserver", "tenant:blocked", ToolAccessPolicy(deny_list=("delete_item",))
        )

        identity_ctx = self._make_identity("tenant:blocked")
        token = identity_context_var.set(identity_ctx)
        try:
            executor = BatchExecutor()
            calls = [
                CallSpec(
                    index=0,
                    call_id="deny-0",
                    mcp_server="myserver",
                    tool="delete_item",
                    arguments={},
                )
            ]
            result = executor.execute(
                batch_id="deny-batch",
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

    def test_allowed_tenant_invokes_tool(self, mock_context):
        """A tenant with no deny_list for the tool reaches command_bus.send."""
        from mcp_hangar.domain.services import get_tool_access_resolver

        resolver = get_tool_access_resolver()
        # Only tenant:blocked is restricted; tenant:allowed has no policy
        resolver.set_standalone_member_policy(
            "myserver", "tenant:blocked", ToolAccessPolicy(deny_list=("delete_item",))
        )

        identity_ctx = self._make_identity("tenant:allowed")
        token = identity_context_var.set(identity_ctx)
        try:
            executor = BatchExecutor()
            calls = [
                CallSpec(
                    index=0,
                    call_id="allow-0",
                    mcp_server="myserver",
                    tool="delete_item",
                    arguments={},
                )
            ]
            result = executor.execute(
                batch_id="allow-batch",
                calls=calls,
                max_concurrency=1,
                global_timeout=30.0,
                fail_fast=False,
            )
        finally:
            identity_context_var.reset(token)

        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()

    def test_no_identity_context_falls_back_to_server_policy(self, mock_context):
        """identity_context_var=None → server-level resolution, no regression."""
        from mcp_hangar.domain.services import get_tool_access_resolver

        resolver = get_tool_access_resolver()
        # Server denies dangerous_tool at the server level
        resolver.set_mcp_server_policy("myserver", ToolAccessPolicy(deny_list=("dangerous_tool",)))

        # Ensure identity_context_var is not set
        token = identity_context_var.set(None)
        try:
            executor = BatchExecutor()
            calls = [
                CallSpec(
                    index=0,
                    call_id="none-id-0",
                    mcp_server="myserver",
                    tool="dangerous_tool",
                    arguments={},
                )
            ]
            result = executor.execute(
                batch_id="none-id-batch",
                calls=calls,
                max_concurrency=1,
                global_timeout=30.0,
                fail_fast=False,
            )
        finally:
            identity_context_var.reset(token)

        # Server policy must still block the tool
        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolAccessDeniedError"
        mock_context.command_bus.send.assert_not_called()


# ---------------------------------------------------------------------------
# Listing-level tests (hangar_tools must hide what the invoke path denies)
# ---------------------------------------------------------------------------


class TestMemberScopePolicyListing:
    """``hangar_tools`` listing keys the SAME per-tenant policy on the caller.

    Regression: the listing helper previously called ``resolver.filter_tools``
    WITHOUT ``member_id``, so a tool denied for a tenant was rejected on the
    invoke path yet stayed VISIBLE in ``hangar_tools`` -- a fail-open on the
    visibility half of the tool-access-policy claim. These lock the two halves
    together: a tenant-denied tool is HIDDEN, matching the invoke denial.
    """

    def _make_identity(self, tenant_id: str | None) -> IdentityContext:
        caller = CallerIdentity(
            user_id=None,
            agent_id=None,
            session_id=None,
            principal_type="anonymous",
            tenant_id=tenant_id,
        )
        return IdentityContext(caller=caller)

    def _tool(self, name: str) -> Mock:
        t = Mock()
        t.name = name
        t.to_dict.return_value = {"name": name, "description": "", "inputSchema": {}}
        return t

    def _server_ctx(self) -> Mock:
        server_obj = Mock(has_tools=True, state=Mock(value="ready"), tools_predefined=True)
        server_obj.tools.list_tools.return_value = [self._tool("get_item"), self._tool("delete_item")]
        ctx = Mock()
        ctx.get_mcp_server.return_value = server_obj
        return ctx

    def _list_for_tenant(self, tenant_id: str | None) -> set[str]:
        from mcp_hangar.server.tools.mcp_server import _get_tools_for_mcp_server

        ctx = self._server_ctx()
        token = identity_context_var.set(self._make_identity(tenant_id) if tenant_id is not None else None)
        try:
            with patch("mcp_hangar.server.tools.mcp_server.get_context", return_value=ctx):
                result = _get_tools_for_mcp_server("myserver")
        finally:
            identity_context_var.reset(token)
        return {t["name"] for t in result["tools"]}

    def test_listing_hides_tenant_denied_tool(self):
        """A tool denied for the caller tenant is HIDDEN from the listing."""
        from mcp_hangar.domain.services import get_tool_access_resolver

        get_tool_access_resolver().set_standalone_member_policy(
            "myserver", "tenant:blocked", ToolAccessPolicy(deny_list=("delete_item",))
        )

        names = self._list_for_tenant("tenant:blocked")
        assert "delete_item" not in names  # denied for this tenant → hidden
        assert "get_item" in names

    def test_listing_shows_tool_for_unrestricted_tenant(self):
        """A different tenant (no member policy) still sees the tool."""
        from mcp_hangar.domain.services import get_tool_access_resolver

        get_tool_access_resolver().set_standalone_member_policy(
            "myserver", "tenant:blocked", ToolAccessPolicy(deny_list=("delete_item",))
        )

        names = self._list_for_tenant("tenant:allowed")
        assert names == {"get_item", "delete_item"}

    def test_listing_no_identity_falls_back_to_server_policy(self):
        """No caller identity → server-level policy only (backward compat)."""
        from mcp_hangar.domain.services import get_tool_access_resolver

        resolver = get_tool_access_resolver()
        # A member deny must NOT leak to an identity-less caller; a server deny must.
        resolver.set_standalone_member_policy(
            "myserver", "tenant:blocked", ToolAccessPolicy(deny_list=("delete_item",))
        )
        resolver.set_mcp_server_policy("myserver", ToolAccessPolicy(deny_list=("get_item",)))

        names = self._list_for_tenant(None)
        assert "delete_item" in names  # member-only deny does not apply without identity
        assert "get_item" not in names  # server-level deny still applies
