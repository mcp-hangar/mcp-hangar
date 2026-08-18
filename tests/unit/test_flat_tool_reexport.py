"""Unit tests for flat per-tenant tool re-export (issue #232).

Covers:
- front_door + tenant: tools/list returns flat backend tools (no hangar_*),
  filtered by member-scope policy AND withdrawal; two tenants see different lists.
- egress mode: tools/list unchanged (hangar_* present, no flat projection).
- flat call routes through enforcement; denied/withdrawn tool not callable.
- unknown flat name → -32601.
- TOCTOU: tool listed for tenant, then withdrawn → call rejected, not invoked.
- collision: two backends export the same tool name → both skipped + warning.
- factory._maybe_register_flat_tool_handlers wires up in front_door only.

Naming: neutral placeholders only.
  servers  → server_a, server_b
  tools    → read_item, get_item, delete_item
  tenants  → tenant:a, tenant:b
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    ToolProjectionRegistry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import (
    ToolAccessResolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server.flat_tool_projection import (
    _build_flat_map,
    _build_mcp_tool_list,
    register_flat_tool_handlers,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_identity(tenant_id: str | None) -> IdentityContext:
    caller = CallerIdentity(
        user_id=None,
        agent_id=None,
        session_id=None,
        principal_type="anonymous",
        tenant_id=tenant_id,
    )
    return IdentityContext(caller=caller)


def _make_schema(name: str) -> ToolSchema:
    return ToolSchema(
        name=name,
        description=f"Does {name}",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
    )


def _populate_registry(registry: ToolProjectionRegistry, server: str, tools: list[str]) -> None:
    schemas = [_make_schema(t) for t in tools]
    registry.build_from_tools(server, schemas)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_singletons():
    """Reset global singletons before and after each test."""
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


@pytest.fixture
def registry() -> ToolProjectionRegistry:
    """Fresh ToolProjectionRegistry (not the process-global singleton)."""
    return ToolProjectionRegistry()


@pytest.fixture
def resolver() -> ToolAccessResolver:
    """Fresh ToolAccessResolver in front_door mode."""
    r = ToolAccessResolver()
    r.set_topology_mode("front_door")
    return r


# ---------------------------------------------------------------------------
# _build_flat_map — unit tests
# ---------------------------------------------------------------------------


class TestBuildFlatMap:
    """Unit tests for the _build_flat_map helper."""

    def test_active_tools_included(self, registry, resolver):
        """Active, policy-allowed tools appear in the flat map."""
        _populate_registry(registry, "server_a", ["read_item", "get_item"])

        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
        ):
            flat = _build_flat_map("tenant:a")

        assert "read_item" in flat
        assert "get_item" in flat
        assert flat["read_item"] == ("server_a", "read_item")
        assert flat["get_item"] == ("server_a", "get_item")

    def test_withdrawn_tool_excluded(self, registry, resolver):
        """A tool withdrawn for the tenant is absent from the flat map."""
        _populate_registry(registry, "server_a", ["read_item", "delete_item"])
        registry.withdraw("server_a", "delete_item", tenant_id="tenant:a")

        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
        ):
            flat = _build_flat_map("tenant:a")

        assert "read_item" in flat
        assert "delete_item" not in flat

    def test_policy_denied_tool_excluded(self, registry, resolver):
        """A tool denied by member-scope policy is absent from the flat map."""
        _populate_registry(registry, "server_a", ["read_item", "delete_item"])
        resolver.set_standalone_member_policy("server_a", "tenant:a", ToolAccessPolicy(deny_list=("delete_item",)))

        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
        ):
            flat = _build_flat_map("tenant:a")

        assert "read_item" in flat
        assert "delete_item" not in flat

    def test_two_tenants_see_different_lists(self, registry, resolver):
        """tenant:a and tenant:b receive different flat maps when policies differ."""
        _populate_registry(registry, "server_a", ["read_item", "delete_item"])
        # tenant:b cannot use delete_item
        resolver.set_standalone_member_policy("server_a", "tenant:b", ToolAccessPolicy(deny_list=("delete_item",)))

        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
        ):
            flat_a = _build_flat_map("tenant:a")
            flat_b = _build_flat_map("tenant:b")

        assert "delete_item" in flat_a
        assert "delete_item" not in flat_b
        assert "read_item" in flat_a
        assert "read_item" in flat_b

    def test_collision_both_skipped_and_warning_logged(self, registry, resolver, caplog):
        """When two servers expose the same tool name, both are skipped and a warning is logged."""
        _populate_registry(registry, "server_a", ["read_item", "get_item"])
        _populate_registry(registry, "server_b", ["read_item"])  # Collision on read_item

        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
            caplog.at_level(logging.WARNING, logger="mcp_hangar.fastmcp_server.flat_tool_projection"),
        ):
            flat = _build_flat_map("tenant:a")

        # read_item collides → both dropped; get_item is fine
        assert "read_item" not in flat
        assert "get_item" in flat

        # Warning was emitted (check the formatted message, not getMessage())
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("flat_tool_name_collision" in (r.getMessage() or "") for r in warning_records)

    def test_withdrawn_for_all_excluded(self, registry, resolver):
        """A tool withdrawn for ALL tenants is excluded regardless of tenant."""
        _populate_registry(registry, "server_a", ["read_item"])
        registry.withdraw("server_a", "read_item")  # all-tenants withdrawal

        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
        ):
            flat_a = _build_flat_map("tenant:a")
            flat_b = _build_flat_map("tenant:b")

        assert "read_item" not in flat_a
        assert "read_item" not in flat_b

    def test_empty_registry_returns_empty_map(self, registry, resolver):
        """An unpopulated registry yields an empty flat map."""
        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
        ):
            flat = _build_flat_map("tenant:a")

        assert flat == {}


# ---------------------------------------------------------------------------
# _build_mcp_tool_list — unit tests
# ---------------------------------------------------------------------------


class TestBuildMcpToolList:
    """Unit tests for _build_mcp_tool_list."""

    def test_returns_mcp_tool_objects_with_schema(self, registry):
        """Tool list contains MCPTool objects with correct name and schema."""
        _populate_registry(registry, "server_a", ["read_item"])
        flat_map = {"read_item": ("server_a", "read_item")}

        with patch(
            "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
            return_value=registry,
        ):
            tools = _build_mcp_tool_list(flat_map)

        assert len(tools) == 1
        assert tools[0].name == "read_item"
        assert tools[0].description == "Does read_item"

    def test_empty_flat_map_returns_empty_list(self, registry):
        with patch(
            "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
            return_value=registry,
        ):
            tools = _build_mcp_tool_list({})

        assert tools == []


# ---------------------------------------------------------------------------
# register_flat_tool_handlers — async handler tests
# ---------------------------------------------------------------------------


class TestFlatListToolsHandler:
    """Tests for the _flat_list_tools handler registered by register_flat_tool_handlers."""

    @pytest.fixture
    def populated_registry(self, registry):
        _populate_registry(registry, "server_a", ["read_item", "get_item"])
        return registry

    @pytest.mark.asyncio
    async def test_front_door_tenant_sees_flat_tools_no_hangar(self, populated_registry, resolver):
        """In front_door mode, list returns flat backend tools; no hangar_* tools."""
        mcp_mock = MagicMock()
        captured_list_fn = None
        captured_call_fn = None

        def fake_list_tools():
            def decorator(fn):
                nonlocal captured_list_fn
                captured_list_fn = fn
                return fn

            return decorator

        def fake_call_tool(*, validate_input=True):
            def decorator(fn):
                nonlocal captured_call_fn
                captured_call_fn = fn
                return fn

            return decorator

        mcp_mock._mcp_server.list_tools = fake_list_tools
        mcp_mock._mcp_server.call_tool = fake_call_tool

        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=populated_registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
        ):
            register_flat_tool_handlers(mcp_mock)

        assert captured_list_fn is not None

        identity = _make_identity("tenant:a")
        token = identity_context_var.set(identity)
        try:
            with (
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                    return_value=populated_registry,
                ),
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                    return_value=resolver,
                ),
            ):
                result = await captured_list_fn()
        finally:
            identity_context_var.reset(token)

        tool_names = [t.name for t in result.tools]
        assert "read_item" in tool_names
        assert "get_item" in tool_names
        # No hangar_* tools
        assert not any(n.startswith("hangar_") for n in tool_names)

    @pytest.mark.asyncio
    async def test_withdrawal_respected_at_list_time(self, populated_registry, resolver):
        """Withdrawn tool is absent from the list for the affected tenant."""
        populated_registry.withdraw("server_a", "delete_item", tenant_id="tenant:a")
        # Also add delete_item to the registry
        _populate_registry(populated_registry, "server_a", ["read_item", "get_item", "delete_item"])
        populated_registry.withdraw("server_a", "delete_item", tenant_id="tenant:a")

        mcp_mock = MagicMock()
        captured_list_fn = None

        def fake_list_tools():
            def decorator(fn):
                nonlocal captured_list_fn
                captured_list_fn = fn
                return fn

            return decorator

        def fake_call_tool(*, validate_input=True):
            def decorator(fn):
                return fn

            return decorator

        mcp_mock._mcp_server.list_tools = fake_list_tools
        mcp_mock._mcp_server.call_tool = fake_call_tool

        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=populated_registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
        ):
            register_flat_tool_handlers(mcp_mock)

        identity = _make_identity("tenant:a")
        token = identity_context_var.set(identity)
        try:
            with (
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                    return_value=populated_registry,
                ),
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                    return_value=resolver,
                ),
            ):
                assert captured_list_fn is not None
                result = await captured_list_fn()
        finally:
            identity_context_var.reset(token)

        tool_names = [t.name for t in result.tools]
        assert "delete_item" not in tool_names
        assert "read_item" in tool_names

    @pytest.mark.asyncio
    async def test_two_tenants_different_lists(self, populated_registry, resolver):
        """Two tenants with different policies receive different tool lists."""
        _populate_registry(populated_registry, "server_a", ["read_item", "delete_item"])
        resolver.set_standalone_member_policy("server_a", "tenant:b", ToolAccessPolicy(deny_list=("delete_item",)))

        mcp_mock = MagicMock()
        captured_list_fn = None

        def fake_list_tools():
            def decorator(fn):
                nonlocal captured_list_fn
                captured_list_fn = fn
                return fn

            return decorator

        def fake_call_tool(*, validate_input=True):
            def decorator(fn):
                return fn

            return decorator

        mcp_mock._mcp_server.list_tools = fake_list_tools
        mcp_mock._mcp_server.call_tool = fake_call_tool

        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=populated_registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
        ):
            register_flat_tool_handlers(mcp_mock)

        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=populated_registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
        ):
            token_a = identity_context_var.set(_make_identity("tenant:a"))
            try:
                assert captured_list_fn is not None
                result_a = await captured_list_fn()
            finally:
                identity_context_var.reset(token_a)

            token_b = identity_context_var.set(_make_identity("tenant:b"))
            try:
                assert captured_list_fn is not None
                result_b = await captured_list_fn()
            finally:
                identity_context_var.reset(token_b)

        names_a = {t.name for t in result_a.tools}
        names_b = {t.name for t in result_b.tools}
        assert "delete_item" in names_a
        assert "delete_item" not in names_b
        assert "read_item" in names_a
        assert "read_item" in names_b

        # Cross-tenant cache isolation (issue #292): each tenant's list carries a
        # distinct SEP-2549 cacheScope so a downstream cache cannot cross tenants.
        assert result_a.meta is not None
        assert result_b.meta is not None
        assert result_a.meta["cacheScope"] != result_b.meta["cacheScope"]


# ---------------------------------------------------------------------------
# flat call dispatch — async handler tests
# ---------------------------------------------------------------------------


class TestFlatCallToolHandler:
    """Tests for the _flat_call_tool handler registered by register_flat_tool_handlers."""

    def _capture_handlers(self, registry, resolver):
        """Register flat handlers on a mock MCP server, return (list_fn, call_fn)."""
        mcp_mock = MagicMock()
        captured = {}

        def fake_list_tools():
            def decorator(fn):
                captured["list"] = fn
                return fn

            return decorator

        def fake_call_tool(*, validate_input=True):
            def decorator(fn):
                captured["call"] = fn
                return fn

            return decorator

        mcp_mock._mcp_server.list_tools = fake_list_tools
        mcp_mock._mcp_server.call_tool = fake_call_tool

        with (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
        ):
            register_flat_tool_handlers(mcp_mock)

        return captured["list"], captured["call"]

    @pytest.mark.asyncio
    async def test_unknown_flat_name_raises_mcp_error_32601(self, registry, resolver):
        """Calling an unknown flat tool name raises McpError with -32601."""
        from mcp_hangar._sdk_compat import McpError
        from mcp_hangar._sdk_compat import METHOD_NOT_FOUND

        _populate_registry(registry, "server_a", ["read_item"])
        _, call_fn = self._capture_handlers(registry, resolver)

        identity = _make_identity("tenant:a")
        token = identity_context_var.set(identity)
        try:
            with (
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                    return_value=registry,
                ),
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                    return_value=resolver,
                ),
            ):
                with pytest.raises(McpError) as exc_info:
                    await call_fn("nonexistent_tool", {})
        finally:
            identity_context_var.reset(token)

        assert exc_info.value.error.code == METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_valid_call_routes_through_enforcement_and_returns_result(self, registry, resolver):
        """A valid flat call goes through BatchExecutor and returns the backend result."""
        _populate_registry(registry, "server_a", ["read_item"])
        _, call_fn = self._capture_handlers(registry, resolver)

        from mcp_hangar.server.tools.batch.models import BatchResult, CallResult

        mock_batch_result = BatchResult(
            batch_id="test",
            success=True,
            total=1,
            succeeded=1,
            failed=0,
            elapsed_ms=1.0,
            results=[
                CallResult(
                    index=0,
                    call_id="test",
                    success=True,
                    result={"data": "value_from_server_a"},
                    elapsed_ms=1.0,
                )
            ],
        )

        mock_executor = Mock()
        mock_executor.execute.return_value = mock_batch_result

        identity = _make_identity("tenant:a")
        token = identity_context_var.set(identity)
        try:
            with (
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                    return_value=registry,
                ),
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                    return_value=resolver,
                ),
                # Patched where the handler's deferred import resolves it
                # (#894). There is no module-level name on the projection to
                # patch any more, and there must not be one.
                patch(
                    "mcp_hangar.server.tools.batch.BatchExecutor",
                    return_value=mock_executor,
                ),
            ):
                result = await call_fn("read_item", {"x": "hello"})
        finally:
            identity_context_var.reset(token)

        assert result == {"data": "value_from_server_a"}
        # BatchExecutor.execute was called with server_a and tool read_item
        call_args = mock_executor.execute.call_args
        calls = call_args.kwargs["calls"]
        assert calls[0].mcp_server == "server_a"
        assert calls[0].tool == "read_item"
        assert calls[0].arguments == {"x": "hello"}

    @pytest.mark.asyncio
    async def test_denied_tool_not_callable(self, registry, resolver):
        """A tool denied by policy returns an isError CallToolResult, not invoked on backend."""
        _populate_registry(registry, "server_a", ["read_item", "delete_item"])
        # Deny delete_item for tenant:a
        resolver.set_standalone_member_policy("server_a", "tenant:a", ToolAccessPolicy(deny_list=("delete_item",)))
        _, call_fn = self._capture_handlers(registry, resolver)

        identity = _make_identity("tenant:a")
        token = identity_context_var.set(identity)
        try:
            with (
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                    return_value=registry,
                ),
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                    return_value=resolver,
                ),
            ):
                # delete_item is denied → absent from flat map → raises -32601
                from mcp_hangar._sdk_compat import McpError

                with pytest.raises(McpError):
                    await call_fn("delete_item", {})
        finally:
            identity_context_var.reset(token)

    @pytest.mark.asyncio
    async def test_toctou_tool_withdrawn_between_list_and_call(self, registry, resolver):
        """TOCTOU: tool was in list but withdrawn before call → call rejected."""
        _populate_registry(registry, "server_a", ["read_item"])
        _, call_fn = self._capture_handlers(registry, resolver)

        # Simulate: after list was served, read_item is withdrawn for tenant:a.
        registry.withdraw("server_a", "read_item", tenant_id="tenant:a")

        from mcp_hangar.server.tools.batch.models import BatchResult, CallResult

        # BatchExecutor should report it withdrawn (ToolWithdrawnError)
        mock_batch_result = BatchResult(
            batch_id="toctou-test",
            success=False,
            total=1,
            succeeded=0,
            failed=1,
            elapsed_ms=1.0,
            results=[
                CallResult(
                    index=0,
                    call_id="toctou-test",
                    success=False,
                    error="Tool 'read_item' is withdrawn for this tenant",
                    error_type="ToolWithdrawnError",
                    elapsed_ms=1.0,
                )
            ],
        )

        mock_executor = Mock()
        mock_executor.execute.return_value = mock_batch_result

        identity = _make_identity("tenant:a")
        token = identity_context_var.set(identity)
        try:
            with (
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                    return_value=registry,
                ),
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                    return_value=resolver,
                ),
                patch(
                    "mcp_hangar.server.tools.batch.BatchExecutor",
                    return_value=mock_executor,
                ),
            ):
                # The tool is now withdrawn, so it won't be in the flat map.
                # Call should yield -32601 (absent from map after withdrawal).
                from mcp_hangar._sdk_compat import McpError

                with pytest.raises(McpError) as exc_info:
                    await call_fn("read_item", {})
        finally:
            identity_context_var.reset(token)

        from mcp_hangar._sdk_compat import METHOD_NOT_FOUND

        assert exc_info.value.error.code == METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_enforcement_failure_surfaces_as_tool_error(self, registry, resolver):
        """When BatchExecutor reports failure, call returns CallToolResult(isError=True)."""
        _populate_registry(registry, "server_a", ["read_item"])
        _, call_fn = self._capture_handlers(registry, resolver)

        from mcp_hangar.server.tools.batch.models import BatchResult, CallResult
        from mcp_hangar._sdk_compat import CallToolResult

        mock_batch_result = BatchResult(
            batch_id="enf-test",
            success=False,
            total=1,
            succeeded=0,
            failed=1,
            elapsed_ms=1.0,
            results=[
                CallResult(
                    index=0,
                    call_id="enf-test",
                    success=False,
                    error="Tool not available for this mcp_server",
                    error_type="ToolAccessDeniedError",
                    elapsed_ms=1.0,
                )
            ],
        )

        mock_executor = Mock()
        mock_executor.execute.return_value = mock_batch_result

        identity = _make_identity("tenant:a")
        token = identity_context_var.set(identity)
        try:
            with (
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                    return_value=registry,
                ),
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                    return_value=resolver,
                ),
                patch(
                    "mcp_hangar.server.tools.batch.BatchExecutor",
                    return_value=mock_executor,
                ),
            ):
                result = await call_fn("read_item", {})
        finally:
            identity_context_var.reset(token)

        assert isinstance(result, CallToolResult)
        # SDK v2 renamed isError -> is_error; accept either.
        assert getattr(result, "is_error", getattr(result, "isError", None)) is True


# ---------------------------------------------------------------------------
# Factory integration — mode-gated registration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Groups behind the front door (#857)
# ---------------------------------------------------------------------------


def _fake_group(group_id: str, member_ids: list[str]):
    from types import SimpleNamespace

    return SimpleNamespace(id=group_id, members=[SimpleNamespace(id=m) for m in member_ids])


class TestGroupsBehindFrontDoor:
    """Members of one group are ONE logical server, not a name collision."""

    def _patches(self, registry, resolver, groups):
        return (
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry",
                return_value=registry,
            ),
            patch(
                "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver",
                return_value=resolver,
            ),
            patch.dict("mcp_hangar.server.bootstrap.composition.GROUPS", groups, clear=True),
        )

    def test_group_members_do_not_collide_with_each_other(self, registry, resolver, caplog):
        """The #857 shape: a group collided with itself and contributed nothing."""
        _populate_registry(registry, "search-v1", ["read_item", "get_item"])
        _populate_registry(registry, "search-v2", ["read_item", "get_item"])
        groups = {"search": _fake_group("search", ["search-v1", "search-v2"])}

        p1, p2, p3 = self._patches(registry, resolver, groups)
        with p1, p2, p3, caplog.at_level(logging.WARNING, logger="mcp_hangar.fastmcp_server.flat_tool_projection"):
            flat = _build_flat_map("tenant:a")

        assert flat["read_item"] == ("search-v1", "read_item")
        assert flat["get_item"] == ("search-v1", "get_item")
        assert not any("flat_tool_name_collision" in r.getMessage() for r in caplog.records)

    def test_cross_backend_collision_still_drops_both(self, registry, resolver, caplog):
        """A group and an unrelated server sharing a name is still ambiguous."""
        _populate_registry(registry, "search-v1", ["read_item"])
        _populate_registry(registry, "server_b", ["read_item"])
        groups = {"search": _fake_group("search", ["search-v1"])}

        p1, p2, p3 = self._patches(registry, resolver, groups)
        with p1, p2, p3, caplog.at_level(logging.WARNING, logger="mcp_hangar.fastmcp_server.flat_tool_projection"):
            flat = _build_flat_map("tenant:a")

        assert "read_item" not in flat
        assert any("flat_tool_name_collision" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_group_call_dispatches_to_the_group_id(self, registry, resolver):
        """The call targets the GROUP, so member selection stays with its strategy."""
        _populate_registry(registry, "search-v1", ["read_item"])
        groups = {"search": _fake_group("search", ["search-v1", "search-v2"])}

        from mcp_hangar.server.tools.batch.models import BatchResult, CallResult

        mock_executor = Mock()
        mock_executor.execute.return_value = BatchResult(
            batch_id="t",
            success=True,
            total=1,
            succeeded=1,
            failed=0,
            elapsed_ms=1.0,
            results=[CallResult(index=0, call_id="t", success=True, result={"ok": 1}, elapsed_ms=1.0)],
        )

        captured = {}
        mcp_mock = MagicMock()

        def fake_list_tools():
            def decorator(fn):
                captured["list"] = fn
                return fn

            return decorator

        def fake_call_tool(*, validate_input=True):
            def decorator(fn):
                captured["call"] = fn
                return fn

            return decorator

        mcp_mock._mcp_server.list_tools = fake_list_tools
        mcp_mock._mcp_server.call_tool = fake_call_tool

        p1, p2, p3 = self._patches(registry, resolver, groups)
        with p1, p2:
            register_flat_tool_handlers(mcp_mock)

        identity = _make_identity("tenant:a")
        token = identity_context_var.set(identity)
        try:
            p1, p2, p3 = self._patches(registry, resolver, groups)
            with (
                p1,
                p2,
                p3,
                patch("mcp_hangar.server.tools.batch.BatchExecutor", return_value=mock_executor),
            ):
                result = await captured["call"]("read_item", {"x": "1"})
        finally:
            identity_context_var.reset(token)

        assert result == {"ok": 1}
        calls = mock_executor.execute.call_args.kwargs["calls"]
        assert calls[0].mcp_server == "search"
        assert calls[0].tool == "read_item"
