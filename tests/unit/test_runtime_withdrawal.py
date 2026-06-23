"""Unit tests for runtime tool withdrawal/restore (#235).

Covers:
- withdraw(server, tool, tenant) → next executor call for that tenant is rejected.
- restore(...) → tool no longer withdrawn (runtime overlay only).
- Reload-safety: runtime withdrawal SURVIVES clear_config_withdrawals().
- Config + runtime compose: both independently block; restoring runtime
  leaves config withdrawal in place.
- API: admin (lifecycle perm) can withdraw/restore; non-admin / unauthenticated
  is rejected (403 / MissingCredentialsError).
- ToolWithdrawn / ToolRestored events published.
"""

from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.read_models.tool_projection import (
    ToolProjectionRegistry,
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.events import ToolRestored, ToolWithdrawn, ToolWithdrawnRejected
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SERVER = "runtime_server"
_TOOL = "runtime_tool"
_TENANT_A = "tenant:alpha"
_TENANT_B = "tenant:beta"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str = _TOOL) -> ToolSchema:
    return ToolSchema(
        name=name,
        description="Runtime test tool",
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
                call_id="rt-call",
                mcp_server=server,
                tool=tool,
                arguments={},
            )
        ]
        return executor.execute(
            batch_id="rt-batch",
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
# Registry unit tests: overlay shape and semantics
# ---------------------------------------------------------------------------


class TestRuntimeWithdrawalOverlay:
    """ToolProjectionRegistry runtime-withdrawal overlay."""

    def test_withdraw_globally_blocks_all_tenants(self, registry: ToolProjectionRegistry):
        registry.withdraw(_SERVER, _TOOL, tenant_id=None)

        proj = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A)
        assert proj is not None
        assert proj.is_withdrawn_for(_TENANT_A)
        assert proj.is_withdrawn_for(_TENANT_B)
        assert proj.is_withdrawn_for(None)

    def test_withdraw_per_tenant_only_blocks_that_tenant(self, registry: ToolProjectionRegistry):
        registry.withdraw(_SERVER, _TOOL, tenant_id=_TENANT_A)

        proj_a = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A)
        assert proj_a is not None
        assert proj_a.is_withdrawn_for(_TENANT_A)

        # Tenant B: overlay doesn't apply → None (safe allow, tool not discovered).
        proj_b = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_B)
        assert proj_b is None

    def test_restore_removes_runtime_withdrawal(self, registry: ToolProjectionRegistry):
        registry.withdraw(_SERVER, _TOOL, tenant_id=None)
        assert registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A) is not None

        registry.restore(_SERVER, _TOOL, tenant_id=None)

        # After restore: not discovered, no overlay → None (safe allow).
        assert registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A) is None

    def test_restore_per_tenant_only_removes_that_tenant(self, registry: ToolProjectionRegistry):
        registry.withdraw(_SERVER, _TOOL, tenant_id=_TENANT_A)
        registry.withdraw(_SERVER, _TOOL, tenant_id=_TENANT_B)

        registry.restore(_SERVER, _TOOL, tenant_id=_TENANT_A)

        # A is no longer withdrawn.
        proj_a = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A)
        assert proj_a is None

        # B still is.
        proj_b = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_B)
        assert proj_b is not None
        assert proj_b.is_withdrawn_for(_TENANT_B)

    def test_runtime_withdrawal_survives_clear_config_withdrawals(
        self, registry: ToolProjectionRegistry
    ):
        """KEY TEST: clear_config_withdrawals() must NOT erase runtime withdrawals."""
        registry.withdraw(_SERVER, _TOOL, tenant_id=None)
        # Simulate a config reload (only clears config overlay, not runtime).
        registry.clear_config_withdrawals()

        proj = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A)
        assert proj is not None, "runtime withdrawal must survive clear_config_withdrawals()"
        assert proj.is_withdrawn_for(_TENANT_A), "tool must still be withdrawn after config reload"

    def test_config_and_runtime_compose(self, registry: ToolProjectionRegistry):
        """Config-withdrawn OR runtime-withdrawn both block independently."""
        registry.set_config_withdrawal(_SERVER, _TOOL, tenant_id=_TENANT_A)
        registry.withdraw(_SERVER, _TOOL, tenant_id=_TENANT_B)

        proj_a = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A)
        assert proj_a is not None and proj_a.is_withdrawn_for(_TENANT_A)

        proj_b = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_B)
        assert proj_b is not None and proj_b.is_withdrawn_for(_TENANT_B)

    def test_restoring_runtime_leaves_config_withdrawal(self, registry: ToolProjectionRegistry):
        """Restoring runtime withdrawal for tenant A keeps config withdrawal for A active."""
        registry.set_config_withdrawal(_SERVER, _TOOL, tenant_id=_TENANT_A)
        registry.withdraw(_SERVER, _TOOL, tenant_id=_TENANT_A)

        # Restore only the runtime overlay.
        registry.restore(_SERVER, _TOOL, tenant_id=_TENANT_A)

        # Config withdrawal still blocks tenant A.
        proj = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A)
        assert proj is not None, "config withdrawal must persist after runtime restore"
        assert proj.is_withdrawn_for(_TENANT_A)

    def test_invalidate_clears_runtime_overlay(self, registry: ToolProjectionRegistry):
        """invalidate() (full reset for tests) also clears runtime withdrawals."""
        registry.withdraw(_SERVER, _TOOL, tenant_id=None)
        registry.invalidate()
        assert registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A) is None


# ---------------------------------------------------------------------------
# Executor enforcement: runtime withdrawal
# ---------------------------------------------------------------------------


class TestRuntimeWithdrawalEndToEnd:
    """BatchExecutor enforces runtime withdrawals via the #231 check."""

    def test_withdraw_blocks_next_call(self, mock_context):
        """withdraw() takes effect on the next call, no reload needed."""
        registry = get_tool_projection_registry()
        registry.withdraw(_SERVER, _TOOL, tenant_id=_TENANT_A)

        result = _execute(mock_context, tenant_id=_TENANT_A)
        assert result.results[0].success is False
        assert result.results[0].error_type == "ToolWithdrawnError"
        mock_context.command_bus.send.assert_not_called()

    def test_restore_unblocks_tool(self, mock_context):
        """restore() removes the block for the next call."""
        registry = get_tool_projection_registry()
        registry.withdraw(_SERVER, _TOOL, tenant_id=_TENANT_A)
        result = _execute(mock_context, tenant_id=_TENANT_A)
        assert result.results[0].success is False

        registry.restore(_SERVER, _TOOL, tenant_id=_TENANT_A)
        mock_context.command_bus.reset_mock()
        result = _execute(mock_context, tenant_id=_TENANT_A)
        assert result.results[0].success is True
        mock_context.command_bus.send.assert_called_once()

    def test_runtime_withdrawal_survives_reload(self, mock_context):
        """Runtime withdrawal survives a config reload (clear_config_withdrawals)."""
        registry = get_tool_projection_registry()
        registry.withdraw(_SERVER, _TOOL, tenant_id=_TENANT_A)

        # Simulate config reload — must NOT clear runtime withdrawal.
        registry.clear_config_withdrawals()

        result = _execute(mock_context, tenant_id=_TENANT_A)
        assert result.results[0].success is False, (
            "runtime withdrawal must survive config reload"
        )
        assert result.results[0].error_type == "ToolWithdrawnError"

    def test_withdraw_emits_rejected_event(self, mock_context):
        """ToolWithdrawnRejected is published when a runtime-withdrawn tool is called."""
        registry = get_tool_projection_registry()
        registry.withdraw(_SERVER, _TOOL, tenant_id=_TENANT_A)

        _execute(mock_context, tenant_id=_TENANT_A)

        events = [
            c.args[0]
            for c in mock_context.event_bus.publish.call_args_list
            if isinstance(c.args[0], ToolWithdrawnRejected)
        ]
        assert len(events) == 1
        assert events[0].tenant_id == _TENANT_A
        assert events[0].mcp_server == _SERVER
        assert events[0].tool == _TOOL


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


def _make_api_context(event_bus=None):
    """Build a minimal ApplicationContext mock for the admin API handler."""
    ctx = Mock()
    ctx.event_bus = event_bus or Mock()
    ctx.auth_components = None  # no authz by default (auth disabled)
    return ctx


@pytest.fixture()
def api_client_no_auth():
    """TestClient with no auth_components — simulates auth-disabled deployment."""
    from mcp_hangar.server.api.router import create_api_router

    event_bus = Mock()
    ctx = _make_api_context(event_bus=event_bus)

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=ctx):
        with patch("mcp_hangar.server.api.admin_tools.get_context", return_value=ctx):
            app = create_api_router(auth_components=None)
            client = TestClient(app, raise_server_exceptions=False)
            yield client, event_bus


@pytest.fixture()
def api_client_with_auth():
    """TestClient where authz is wired and enforced."""
    from mcp_hangar.domain.exceptions import AccessDeniedError, MissingCredentialsError
    from mcp_hangar.server.api.router import create_api_router

    event_bus = Mock()
    ctx = Mock()
    ctx.event_bus = event_bus

    # authz_middleware that enforces admin (lifecycle action on mcp_servers)
    def _authorize(*, principal, action, resource_type, resource_id):
        if principal.is_anonymous():
            raise MissingCredentialsError("Authentication required")
        if getattr(principal, "_role", None) != "admin":
            raise AccessDeniedError(
                principal_id=str(principal.id),
                action=action,
                resource=resource_type,
            )

    authz = Mock()
    authz.authorize.side_effect = _authorize
    auth_components = Mock()
    auth_components.enabled = False  # authn middleware off (we inject auth context manually)
    auth_components.authz_middleware = authz
    ctx.auth_components = auth_components

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=ctx):
        with patch("mcp_hangar.server.api.admin_tools.get_context", return_value=ctx):
            with patch("mcp_hangar.server.api.mcp_servers.get_context", return_value=ctx):
                app = create_api_router(auth_components=auth_components)
                client = TestClient(app, raise_server_exceptions=False)
                yield client, event_bus


class TestAdminToolsAPINoAuth:
    """Admin endpoints work when auth is not configured (no authz check)."""

    def test_withdraw_returns_200(self, api_client_no_auth):
        client, event_bus = api_client_no_auth
        response = client.post(
            f"/admin/tools/{_SERVER}/{_TOOL}/withdraw",
            json={"tenant_id": _TENANT_A},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["withdrawn"] is True
        assert data["mcp_server"] == _SERVER
        assert data["tool"] == _TOOL
        assert data["tenant_id"] == _TENANT_A

    def test_withdraw_publishes_event(self, api_client_no_auth):
        client, event_bus = api_client_no_auth
        client.post(
            f"/admin/tools/{_SERVER}/{_TOOL}/withdraw",
            json={"tenant_id": _TENANT_A},
        )
        published = [c.args[0] for c in event_bus.publish.call_args_list]
        withdrawn_events = [e for e in published if isinstance(e, ToolWithdrawn)]
        assert len(withdrawn_events) == 1
        assert withdrawn_events[0].mcp_server == _SERVER
        assert withdrawn_events[0].tool == _TOOL
        assert withdrawn_events[0].tenant_id == _TENANT_A

    def test_restore_returns_200(self, api_client_no_auth):
        client, event_bus = api_client_no_auth
        # Withdraw first so there's something to restore.
        client.post(
            f"/admin/tools/{_SERVER}/{_TOOL}/withdraw",
            json={"tenant_id": _TENANT_A},
        )
        response = client.post(
            f"/admin/tools/{_SERVER}/{_TOOL}/restore",
            json={"tenant_id": _TENANT_A},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["restored"] is True

    def test_restore_publishes_event(self, api_client_no_auth):
        client, event_bus = api_client_no_auth
        event_bus.reset_mock()
        client.post(
            f"/admin/tools/{_SERVER}/{_TOOL}/restore",
            json={"tenant_id": _TENANT_A},
        )
        published = [c.args[0] for c in event_bus.publish.call_args_list]
        restored_events = [e for e in published if isinstance(e, ToolRestored)]
        assert len(restored_events) == 1
        assert restored_events[0].mcp_server == _SERVER
        assert restored_events[0].tool == _TOOL

    def test_withdraw_global_no_body(self, api_client_no_auth):
        """Empty body → global withdrawal (tenant_id=None)."""
        client, _ = api_client_no_auth
        response = client.post(f"/admin/tools/{_SERVER}/{_TOOL}/withdraw")
        assert response.status_code == 200
        data = response.json()
        assert data["tenant_id"] is None

    def test_withdraw_persists_in_registry(self, api_client_no_auth):
        """After API withdraw, the registry reflects the withdrawal."""
        client, _ = api_client_no_auth
        client.post(
            f"/admin/tools/{_SERVER}/{_TOOL}/withdraw",
            json={"tenant_id": _TENANT_A},
        )
        registry = get_tool_projection_registry()
        proj = registry.resolve(_SERVER, _TOOL, tenant_id=_TENANT_A)
        assert proj is not None
        assert proj.is_withdrawn_for(_TENANT_A)


class TestAdminToolsAPIWithAuth:
    """Admin endpoints enforce the lifecycle permission when auth is enabled."""

    def _inject_principal(self, client: TestClient, role: str | None):
        """Return headers that inject a mock principal via a middleware hack.

        Because we disabled authn middleware and are testing authz only,
        we use a custom middleware injected per-request to set state.auth.
        We instead patch get_context to return a context where auth_components
        enforces the role-based check.
        """

    def test_anonymous_rejected(self, api_client_with_auth):
        """Unauthenticated request → 403 (MissingCredentialsError)."""
        client, _ = api_client_with_auth
        # No auth context injected → principal is None → is_anonymous() → 403.
        response = client.post(f"/admin/tools/{_SERVER}/{_TOOL}/withdraw", json={})
        # _check_permission raises MissingCredentialsError which maps to 403 via AccessDeniedError
        assert response.status_code in (401, 403)

    def test_non_admin_rejected(self, api_client_with_auth):
        """Non-admin principal → AccessDeniedError → 403."""
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request as StarletteRequest

        client, _ = api_client_with_auth

        # Build a non-admin principal
        non_admin = Mock()
        non_admin._role = "developer"
        non_admin.id = "dev-user"
        non_admin.is_anonymous = Mock(return_value=False)
        mock_auth = Mock()
        mock_auth.principal = non_admin

        class InjectAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: StarletteRequest, call_next):
                request.state.auth = mock_auth
                return await call_next(request)

        client.app.add_middleware(InjectAuthMiddleware)  # type: ignore[attr-defined]

        response = client.post(f"/admin/tools/{_SERVER}/{_TOOL}/withdraw", json={})
        assert response.status_code == 403
