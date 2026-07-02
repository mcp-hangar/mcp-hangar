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
        from mcp.shared.exceptions import McpError
        from mcp.types import METHOD_NOT_FOUND

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
                patch(
                    "mcp_hangar.fastmcp_server.flat_tool_projection.BatchExecutor",
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
                from mcp.shared.exceptions import McpError

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
                    "mcp_hangar.fastmcp_server.flat_tool_projection.BatchExecutor",
                    return_value=mock_executor,
                ),
            ):
                # The tool is now withdrawn, so it won't be in the flat map.
                # Call should yield -32601 (absent from map after withdrawal).
                from mcp.shared.exceptions import McpError

                with pytest.raises(McpError) as exc_info:
                    await call_fn("read_item", {})
        finally:
            identity_context_var.reset(token)

        from mcp.types import METHOD_NOT_FOUND

        assert exc_info.value.error.code == METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_enforcement_failure_surfaces_as_tool_error(self, registry, resolver):
        """When BatchExecutor reports failure, call returns CallToolResult(isError=True)."""
        _populate_registry(registry, "server_a", ["read_item"])
        _, call_fn = self._capture_handlers(registry, resolver)

        from mcp_hangar.server.tools.batch.models import BatchResult, CallResult
        from mcp.types import CallToolResult

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
                    "mcp_hangar.fastmcp_server.flat_tool_projection.BatchExecutor",
                    return_value=mock_executor,
                ),
            ):
                result = await call_fn("read_item", {})
        finally:
            identity_context_var.reset(token)

        assert isinstance(result, CallToolResult)
        assert result.isError is True


# ---------------------------------------------------------------------------
# Factory integration — mode-gated registration
# ---------------------------------------------------------------------------


class TestFactoryModeGate:
    """MCPServerFactory registers flat handlers only in front_door mode."""

    def _make_hangar(self):
        from mcp_hangar.fastmcp_server import HangarFunctions

        return HangarFunctions(
            list=Mock(return_value={"mcp_servers": []}),
            start=Mock(return_value={"status": "started"}),
            stop=Mock(return_value={"status": "stopped"}),
            invoke=Mock(return_value={"result": 42}),
            tools=Mock(return_value={"tools": []}),
            details=Mock(return_value={"mcp_server": "test"}),
            health=Mock(return_value={"status": "healthy"}),
        )

    def test_front_door_registers_flat_handlers(self):
        """In front_door mode, _maybe_register_flat_tool_handlers is called with the MCP instance."""
        from mcp_hangar.fastmcp_server import MCPServerFactory
        from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver

        resolver = get_tool_access_resolver()
        resolver.set_topology_mode("front_door")

        # Patch the static method so we can verify it was called
        with patch.object(
            MCPServerFactory,
            "_maybe_register_flat_tool_handlers",
        ) as mock_maybe:
            factory = MCPServerFactory(self._make_hangar())
            mcp = factory.create_server()
            mock_maybe.assert_called_once_with(mcp)

    def test_egress_mode_does_not_register_flat_handlers(self):
        """In egress mode, register_flat_tool_handlers is NOT called."""
        from mcp_hangar.fastmcp_server import MCPServerFactory
        from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver

        resolver = get_tool_access_resolver()
        resolver.set_topology_mode("egress")

        with patch("mcp_hangar.fastmcp_server.flat_tool_projection.register_flat_tool_handlers") as mock_register:
            factory = MCPServerFactory(self._make_hangar())
            factory.create_server()
            mock_register.assert_not_called()

    def test_default_egress_mode_unchanged(self):
        """Default (egress) mode: tools/list is not replaced (hangar_* surface intact)."""
        from mcp_hangar.fastmcp_server import MCPServerFactory
        from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver

        # Default mode is egress — do not set anything.
        resolver = get_tool_access_resolver()
        assert resolver.topology_mode == "egress"

        with patch("mcp_hangar.fastmcp_server.flat_tool_projection.register_flat_tool_handlers") as mock_register:
            factory = MCPServerFactory(self._make_hangar())
            factory.create_server()
            mock_register.assert_not_called()
