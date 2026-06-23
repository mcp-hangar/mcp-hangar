"""Unit tests for tool withdrawal call-path enforcement (#231).

Guarantee framing (must be reflected here and in code comments):
- Enforcement is **per-process-after-reload**: the ToolProjectionRegistry is
  config-reload-driven (#230); withdrawal takes effect on the replica that
  reloaded. Runtime mutation without reload is #235.
- Rejection is **envelope-level**: CallResult(success=False, error_type='ToolWithdrawnError').
  Protocol-clean JSON-RPC -32601 on a single tools/call is #232-gated.
"""

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.events import ToolWithdrawnRejected
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERVER = "server_a"
_TOOL = "read_item"


def _make_tool(name: str = _TOOL) -> ToolSchema:
    return ToolSchema(
        name=name,
        description="A tool",
        input_schema={"type": "object", "properties": {}},
    )


def _make_identity(tenant_id: str | None) -> IdentityContext:
    caller = CallerIdentity(
        user_id=None,
        agent_id=None,
        session_id=None,
        principal_type="anonymous",
        tenant_id=tenant_id,
    )
    return IdentityContext(caller=caller)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset both global singletons before and after each test."""
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
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


def _execute(mock_context, tenant_id: str | None, tool: str = _TOOL) -> object:
    """Run a single-call batch for *tenant_id* against *tool* and return the result."""
    identity_ctx = _make_identity(tenant_id)
    token = identity_context_var.set(identity_ctx)
    try:
        executor = BatchExecutor()
        calls = [
            CallSpec(
                index=0,
                call_id="test-call",
                mcp_server=_SERVER,
                tool=tool,
                arguments={},
            )
        ]
        return executor.execute(
            batch_id="test-batch",
            calls=calls,
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )
    finally:
        identity_context_var.reset(token)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolWithdrawalEnforcement:
    """BatchExecutor enforces tool withdrawal via ToolProjectionRegistry."""

    def test_both_tenants_succeed_when_not_withdrawn(self, mock_context):
        """Tenants A and B both reach command_bus.send when the tool is active (not withdrawn)."""
        registry = get_tool_projection_registry()
        # Populate registry with tool active for all tenants (no tenant_overrides)
        registry.build_from_tools(_SERVER, [_make_tool()])

        for tenant_id in ("tenant:A", "tenant:B"):
            mock_context.command_bus.reset_mock()
            result = _execute(mock_context, tenant_id)
            assert result.results[0].success is True, f"{tenant_id} should succeed when tool is active"
            mock_context.command_bus.send.assert_called_once()

    def test_withdrawn_tenant_is_blocked(self, mock_context):
        """After withdrawing the tool for tenant:A, A's call returns ToolWithdrawnError
        and command_bus.send is NOT called (backend is never reached).

        Per-process-after-reload guarantee: this tests the post-reload steady state.
        Rejection is envelope-level (CallResult success=False), not -32601 (#232-gated).
        """
        registry = get_tool_projection_registry()
        registry.build_from_tools(
            _SERVER,
            [_make_tool()],
            tenant_overrides={_TOOL: {"tenant:A": "withdrawn"}},
        )

        result = _execute(mock_context, "tenant:A")
        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolWithdrawnError"
        mock_context.command_bus.send.assert_not_called()

    def test_non_withdrawn_tenant_still_succeeds(self, mock_context):
        """Tenant B is unaffected when the tool is withdrawn only for tenant:A."""
        registry = get_tool_projection_registry()
        registry.build_from_tools(
            _SERVER,
            [_make_tool()],
            tenant_overrides={_TOOL: {"tenant:A": "withdrawn"}},
        )

        result = _execute(mock_context, "tenant:B")
        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()

    def test_tool_not_in_registry_is_not_blocked(self, mock_context):
        """A tool absent from the registry (proj is None) must NOT be blocked.

        Regression guard: deployments that have not populated the registry
        (unpopulated / empty) must continue to work unaffected. The safe default
        is to allow — only an explicit withdrawn status blocks.
        """
        # Registry is empty (never populated for this server/tool)
        result = _execute(mock_context, "tenant:A")
        # Must succeed — registry absence is not a block signal
        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()

    def test_withdrawn_rejected_event_published(self, mock_context):
        """ToolWithdrawnRejected audit event is published with correct tenant/server/tool."""
        registry = get_tool_projection_registry()
        registry.build_from_tools(
            _SERVER,
            [_make_tool()],
            tenant_overrides={_TOOL: {"tenant:A": "withdrawn"}},
        )

        _execute(mock_context, "tenant:A")

        published_events = [
            call.args[0]
            for call in mock_context.event_bus.publish.call_args_list
            if isinstance(call.args[0], ToolWithdrawnRejected)
        ]
        assert len(published_events) == 1, "Exactly one ToolWithdrawnRejected event expected"
        evt = published_events[0]
        assert evt.tenant_id == "tenant:A"
        assert evt.mcp_server == _SERVER
        assert evt.tool == _TOOL

    def test_base_withdrawn_status_blocks_all_tenants(self, mock_context):
        """A tool withdrawn at base level blocks all tenants (no tenant_overrides needed)."""
        registry = get_tool_projection_registry()
        registry.build_from_tools(
            _SERVER,
            [_make_tool()],
            status_overrides={_TOOL: "withdrawn"},
        )

        result = _execute(mock_context, "tenant:A")
        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolWithdrawnError"
        mock_context.command_bus.send.assert_not_called()

    def test_tenant_override_active_exempts_from_base_withdrawn(self, mock_context):
        """A per-tenant 'active' override exempts that tenant from a base 'withdrawn' status."""
        registry = get_tool_projection_registry()
        registry.build_from_tools(
            _SERVER,
            [_make_tool()],
            status_overrides={_TOOL: "withdrawn"},
            tenant_overrides={_TOOL: {"tenant:exempt": "active"}},
        )

        # Exempt tenant should succeed
        result = _execute(mock_context, "tenant:exempt")
        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()

        # Non-exempt tenant is still blocked
        mock_context.command_bus.reset_mock()
        result = _execute(mock_context, "tenant:other")
        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolWithdrawnError"
        mock_context.command_bus.send.assert_not_called()
