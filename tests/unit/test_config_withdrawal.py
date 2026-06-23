"""Unit tests for config-declared tool withdrawal (#244).

Guarantees:
- Config-declared withdrawn tools are rejected by the executor BEFORE
  discovery (no build_from_tools needed).
- Per-tenant withdrawals apply only to the configured tenant.
- Reload clears prior config withdrawals so removing one from config restores
  the tool.
- End-to-end: executor rejects via the existing #231 check (proj is not None
  and proj.is_withdrawn_for() is True).
"""

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    ToolProjectionRegistry,
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SERVER = "allegro"
_TOOL = "legacy_search"
_TOOL_B = "beta_tool"
_TENANT_A = "tenant:openai"
_TENANT_B = "tenant:other"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str) -> ToolSchema:
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


def _execute(mock_context, tenant_id: str | None, server: str = _SERVER, tool: str = _TOOL):
    """Run a single-call batch for *tenant_id* and return the BatchResult."""
    identity_ctx = _make_identity(tenant_id)
    token = identity_context_var.set(identity_ctx)
    try:
        executor = BatchExecutor()
        calls = [
            CallSpec(
                index=0,
                call_id="test-call",
                mcp_server=server,
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singletons before and after each test."""
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


@pytest.fixture()
def registry():
    """Return the global singleton registry (already reset by autouse fixture)."""
    return get_tool_projection_registry()


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
# Registry overlay unit tests
# ---------------------------------------------------------------------------


class TestConfigWithdrawalOverlay:
    """ToolProjectionRegistry config-withdrawal overlay (no build_from_tools)."""

    def test_global_withdrawal_blocks_before_discovery(self, registry: ToolProjectionRegistry):
        """set_config_withdrawal(tenant_id=None) blocks all tenants even without discovery."""
        registry.set_config_withdrawal(_SERVER, _TOOL, tenant_id=None)

        proj = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A)
        assert proj is not None, "resolve() must return a projection, not None"
        assert proj.is_withdrawn_for(_TENANT_A), "tool must be withdrawn for tenant A"
        assert proj.is_withdrawn_for(_TENANT_B), "tool must be withdrawn for ALL tenants"
        assert proj.is_withdrawn_for(None), "tool must be withdrawn even for anonymous"

    def test_per_tenant_withdrawal_only_blocks_that_tenant(self, registry: ToolProjectionRegistry):
        """Per-tenant config withdrawal blocks only the specified tenant.

        For the non-withdrawn tenant, resolve() returns None (tool not yet
        discovered and the overlay doesn't apply to that tenant) — the safe
        default is not to block.
        """
        registry.set_config_withdrawal(_SERVER, _TOOL_B, tenant_id=_TENANT_A)

        # Tenant A: withdrawn — resolve returns a synthesized withdrawn projection.
        proj_a = registry.resolve(_SERVER, _TOOL_B, tenant_id=_TENANT_A)
        assert proj_a is not None
        assert proj_a.is_withdrawn_for(_TENANT_A)

        # Tenant B: overlay does not apply → resolve returns None (not discovered).
        # None means "not blocked" per the safe-default semantics.
        proj_b = registry.resolve(_SERVER, _TOOL_B, tenant_id=_TENANT_B)
        assert proj_b is None, "undiscovered tool with no overlay for tenant B returns None (safe allow)"

    def test_resolve_returns_none_when_no_config_withdrawal_and_not_discovered(
        self, registry: ToolProjectionRegistry
    ):
        """resolve() returns None when tool is neither config-withdrawn nor discovered."""
        # No withdrawal set, no build_from_tools → safe default: None → not blocked.
        assert registry.resolve(_SERVER, _TOOL) is None

    def test_global_withdrawal_wins_over_per_tenant_withdrawal(self, registry: ToolProjectionRegistry):
        """set_config_withdrawal(None) after per-tenant withdrawal covers all tenants."""
        registry.set_config_withdrawal(_SERVER, _TOOL, tenant_id=_TENANT_A)
        registry.set_config_withdrawal(_SERVER, _TOOL, tenant_id=None)  # ALL

        proj = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_B)
        assert proj is not None
        assert proj.is_withdrawn_for(_TENANT_B)

    def test_clear_config_withdrawals_restores_tool(self, registry: ToolProjectionRegistry):
        """clear_config_withdrawals() removes all overlay entries."""
        registry.set_config_withdrawal(_SERVER, _TOOL, tenant_id=None)
        assert registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A) is not None

        registry.clear_config_withdrawals()

        # After clear: tool is unknown (not discovered) → None
        assert registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A) is None

    def test_invalidate_also_clears_config_withdrawals(self, registry: ToolProjectionRegistry):
        """invalidate() clears both discovered projections and config overlay."""
        registry.set_config_withdrawal(_SERVER, _TOOL, tenant_id=None)
        registry.invalidate()

        assert registry.resolve(_SERVER, _TOOL) is None

    def test_config_withdrawal_overlay_plus_discovered_tool_all_tenants(
        self, registry: ToolProjectionRegistry
    ):
        """When a discovered tool is globally config-withdrawn, it returns withdrawn status."""
        registry.build_from_tools(_SERVER, [_make_tool(_TOOL)])
        registry.set_config_withdrawal(_SERVER, _TOOL, tenant_id=None)

        proj = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_B)
        assert proj is not None
        assert proj.is_withdrawn_for(_TENANT_B), "discovered tool must be withdrawn for all tenants"

    def test_config_withdrawal_overlay_plus_discovered_tool_per_tenant(
        self, registry: ToolProjectionRegistry
    ):
        """Per-tenant overlay merges into discovered tool's tenant_overrides."""
        registry.build_from_tools(_SERVER, [_make_tool(_TOOL)])
        registry.set_config_withdrawal(_SERVER, _TOOL, tenant_id=_TENANT_A)

        proj_a = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A)
        assert proj_a is not None
        assert proj_a.is_withdrawn_for(_TENANT_A)

        proj_b = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_B)
        assert proj_b is not None
        assert not proj_b.is_withdrawn_for(_TENANT_B)


# ---------------------------------------------------------------------------
# Config parsing tests
# ---------------------------------------------------------------------------


class TestConfigParsing:
    """_load_mcp_server_config applies tool_projection.withdrawn to registry."""

    def _apply_spec(self, mcp_server_id: str, spec: dict) -> None:
        """Parse a minimal server spec through _load_mcp_server_config."""
        from mcp_hangar.server.config import _load_mcp_server_config

        _load_mcp_server_config(mcp_server_id, spec)

    @pytest.fixture(autouse=True)
    def stub_repository(self):
        """Stub the mcp_server repository so we don't need a real domain setup."""
        with patch("mcp_hangar.server.config._mcp_server_repository") as mock_repo:
            repo = Mock()
            repo.add = Mock()
            mock_repo.return_value = repo
            yield

    def test_global_withdrawn_list_registered(self):
        """tool_projection.withdrawn list registers ALL-tenant config withdrawals."""
        self._apply_spec(
            _SERVER,
            {
                "mode": "subprocess",
                "command": ["dummy"],
                "tool_projection": {"withdrawn": [_TOOL]},
            },
        )
        registry = get_tool_projection_registry()
        proj = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A)
        assert proj is not None
        assert proj.is_withdrawn_for(_TENANT_A)
        assert proj.is_withdrawn_for(_TENANT_B)

    def test_tenant_overrides_withdrawn_registered(self):
        """tool_projection.tenant_overrides[tenant].withdrawn registers per-tenant withdrawal."""
        self._apply_spec(
            _SERVER,
            {
                "mode": "subprocess",
                "command": ["dummy"],
                "tool_projection": {
                    "tenant_overrides": {
                        _TENANT_A: {"withdrawn": [_TOOL_B]},
                    }
                },
            },
        )
        registry = get_tool_projection_registry()
        proj_a = registry.resolve(_SERVER, _TOOL_B, tenant_id=_TENANT_A)
        assert proj_a is not None
        assert proj_a.is_withdrawn_for(_TENANT_A)

        proj_b = registry.resolve(_SERVER, _TOOL_B, tenant_id=_TENANT_B)
        assert proj_b is None or not proj_b.is_withdrawn_for(_TENANT_B)

    def test_reload_clears_then_reapplies_withdrawals(self):
        """Removing a tool from config.withdrawn restores it after reload."""
        # Simulate first load: tool is withdrawn
        self._apply_spec(
            _SERVER,
            {
                "mode": "subprocess",
                "command": ["dummy"],
                "tool_projection": {"withdrawn": [_TOOL]},
            },
        )
        registry = get_tool_projection_registry()
        assert registry.resolve(_SERVER, _TOOL, _TENANT_A) is not None
        assert registry.resolve(_SERVER, _TOOL, _TENANT_A).is_withdrawn_for(_TENANT_A)

        # Simulate reload: clear withdrawals, re-apply without the tool
        registry.clear_config_withdrawals()
        self._apply_spec(
            _SERVER,
            {
                "mode": "subprocess",
                "command": ["dummy"],
                # tool_projection block absent → no withdrawal
            },
        )

        # After reload without withdrawal, resolve returns None (not discovered, not withdrawn)
        assert registry.resolve(_SERVER, _TOOL, _TENANT_A) is None


# ---------------------------------------------------------------------------
# End-to-end: executor enforces config withdrawal via #231 check
# ---------------------------------------------------------------------------


class TestConfigWithdrawalEndToEnd:
    """Executor rejects config-withdrawn tools via the existing #231 check."""

    def test_config_withdrawn_tool_blocked_before_discovery(self, mock_context):
        """Executor blocks a tool that is config-withdrawn but never discovered.

        This is the key #244 guarantee: no build_from_tools needed; the config
        overlay makes resolve() return a withdrawn projection so #231's
        ``if proj is not None and proj.is_withdrawn_for(tenant)`` fires.
        """
        registry = get_tool_projection_registry()
        registry.set_config_withdrawal(_SERVER, _TOOL, tenant_id=None)

        result = _execute(mock_context, tenant_id=_TENANT_A)
        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolWithdrawnError"
        mock_context.command_bus.send.assert_not_called()

    def test_config_withdrawn_tenant_blocked_other_allowed(self, mock_context):
        """Per-tenant config withdrawal blocks only that tenant; others reach backend."""
        registry = get_tool_projection_registry()
        registry.set_config_withdrawal(_SERVER, _TOOL, tenant_id=_TENANT_A)

        # Tenant A: blocked
        result_a = _execute(mock_context, tenant_id=_TENANT_A)
        assert result_a.results[0].success is False
        assert result_a.results[0].error_type == "ToolWithdrawnError"
        mock_context.command_bus.send.assert_not_called()

        # Tenant B: NOT blocked — resolve returns None for undiscovered tool with no overlay
        mock_context.command_bus.reset_mock()
        result_b = _execute(mock_context, tenant_id=_TENANT_B)
        assert result_b.results[0].success is True
        mock_context.command_bus.send.assert_called_once()

    def test_non_withdrawn_tool_not_blocked(self, mock_context):
        """Tool not in config withdrawals is NOT blocked (None → safe-allow default)."""
        # Registry empty, no withdrawals — resolve returns None → not blocked
        result = _execute(mock_context, tenant_id=_TENANT_A)
        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()

    def test_clearing_withdrawal_unblocks_tool(self, mock_context):
        """After clear_config_withdrawals(), the tool is no longer blocked."""
        registry = get_tool_projection_registry()
        registry.set_config_withdrawal(_SERVER, _TOOL, tenant_id=None)

        # Confirm blocked first
        result = _execute(mock_context, tenant_id=_TENANT_A)
        assert result.results[0].success is False

        # Clear and confirm unblocked
        registry.clear_config_withdrawals()
        mock_context.command_bus.reset_mock()
        result = _execute(mock_context, tenant_id=_TENANT_A)
        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()
