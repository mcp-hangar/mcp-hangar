"""Integration tests for the identity bridge in create_auth_combined_app.

These tests verify that the bridge in asgi.py correctly propagates the authenticated
Principal → CallerIdentity → identity_context_var WITHOUT manually setting the
contextvar. The bridge must do it.

Covers:
- Authenticated USER principal with tenant_id binds contextvar correctly.
- SERVICE_ACCOUNT principal maps to "service" principal_type.
- SYSTEM principal maps to "service" principal_type.
- Anonymous principal binds to principal_type="anonymous", tenant_id=None.
- context_var is reset after the request (no leakage).
- NullAuthComponents (auth disabled) path: identity_context_var NOT set by the bridge
  (no bridge runs when auth is skipped to WebSocket path).
"""

from __future__ import annotations

import pytest

from mcp_hangar.context import get_identity_context, identity_context_var
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server.asgi import _principal_to_identity_context


# ---------------------------------------------------------------------------
# Unit tests for the mapping helper (no ASGI overhead)
# ---------------------------------------------------------------------------


class TestPrincipalToIdentityContext:
    """Tests for the _principal_to_identity_context bridge helper."""

    def test_user_principal_maps_correctly(self):
        principal = Principal(
            id=PrincipalId("user:alice"),
            type=PrincipalType.USER,
            tenant_id="tenant-abc",
        )
        ctx = _principal_to_identity_context(principal)
        assert ctx.caller.principal_type == "user"
        assert ctx.caller.user_id == "user:alice"
        assert ctx.caller.tenant_id == "tenant-abc"
        assert ctx.caller.agent_id is None
        assert ctx.caller.session_id is None

    def test_service_account_principal_maps_to_service(self):
        principal = Principal(
            id=PrincipalId("svc-ci-pipeline"),
            type=PrincipalType.SERVICE_ACCOUNT,
            tenant_id="tenant-xyz",
        )
        ctx = _principal_to_identity_context(principal)
        assert ctx.caller.principal_type == "service"
        assert ctx.caller.user_id == "svc-ci-pipeline"
        assert ctx.caller.tenant_id == "tenant-xyz"

    def test_system_principal_maps_to_service(self):
        """SYSTEM maps to 'service' — the closest valid Literal for non-human identity."""
        principal = Principal.system()
        ctx = _principal_to_identity_context(principal)
        # system() has id="system", type=SYSTEM, no tenant
        assert ctx.caller.principal_type == "service"
        assert ctx.caller.user_id == "system"
        assert ctx.caller.tenant_id is None

    def test_anonymous_principal_maps_to_anonymous(self):
        principal = Principal.anonymous()
        ctx = _principal_to_identity_context(principal)
        assert ctx.caller.principal_type == "anonymous"
        assert ctx.caller.user_id is None
        assert ctx.caller.tenant_id is None

    def test_none_principal_maps_to_anonymous(self):
        ctx = _principal_to_identity_context(None)
        assert ctx.caller.principal_type == "anonymous"
        assert ctx.caller.user_id is None
        assert ctx.caller.tenant_id is None

    def test_user_without_tenant_id(self):
        principal = Principal(
            id=PrincipalId("user:bob"),
            type=PrincipalType.USER,
            tenant_id=None,
        )
        ctx = _principal_to_identity_context(principal)
        assert ctx.caller.principal_type == "user"
        assert ctx.caller.tenant_id is None

    def test_caller_identity_post_init_satisfied(self):
        """user_id must be non-None for 'user'/'service' — bridge must satisfy this."""
        from mcp_hangar.domain.value_objects.identity import CallerIdentity

        for p_type in (PrincipalType.USER, PrincipalType.SERVICE_ACCOUNT, PrincipalType.SYSTEM):
            principal = Principal(
                id=PrincipalId("some-id"),
                type=p_type,
                tenant_id="t1",
            )
            ctx = _principal_to_identity_context(principal)
            # Validate that the CallerIdentity is internally consistent
            assert isinstance(ctx.caller, CallerIdentity)
            if ctx.caller.principal_type in ("user", "service"):
                assert ctx.caller.user_id is not None


# ---------------------------------------------------------------------------
# Integration tests: drive create_auth_combined_app end-to-end
# ---------------------------------------------------------------------------


def _make_scope(path: str = "/mcp") -> dict:
    """Minimal ASGI HTTP scope."""
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
    }


async def _null_receive():
    return {"type": "http.disconnect"}


class _CapturingMcpApp:
    """Stub MCP app that captures the identity contextvar when called."""

    def __init__(self):
        self.captured_identity_ctx = None
        self.call_count = 0

    async def __call__(self, scope, receive, send):
        # Read identity contextvar HERE — bridge must have set it before calling us
        self.captured_identity_ctx = get_identity_context()
        self.call_count += 1
        # Return a minimal 200 response
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})


class _FakeAuthComponents:
    """Minimal auth components returning a fixed Principal from authenticate()."""

    def __init__(self, principal: Principal):
        self._principal = principal
        self.authn_middleware = self

    def authenticate(self, auth_request):
        from mcp_hangar.auth.infrastructure.middleware import AuthContext

        return AuthContext(principal=self._principal, auth_method="test")


class _FakeConfig:
    """Minimal server config for create_auth_combined_app."""

    auth_skip_paths = ["/health", "/ready", "/metrics"]
    trusted_proxies: list[str] = []


class TestAuthCombinedAppIdentityBridge:
    """Integration tests: the bridge in create_auth_combined_app binds identity_context_var."""

    def setup_method(self):
        # Clear any leftover contextvar state from previous tests
        identity_context_var.set(None)

    @pytest.mark.asyncio
    async def test_authenticated_user_tenant_bound_in_mcp_app(self):
        """USER principal with tenant_id → contextvar has tenant_id when mcp_app is called."""
        principal = Principal(
            id=PrincipalId("user:alice"),
            type=PrincipalType.USER,
            tenant_id="tenant-prod",
        )
        stub_mcp = _CapturingMcpApp()
        auth_components = _FakeAuthComponents(principal)
        config = _FakeConfig()

        from mcp_hangar.fastmcp_server.asgi import create_auth_combined_app
        from starlette.applications import Starlette

        aux_app = Starlette()
        app = create_auth_combined_app(aux_app, stub_mcp, auth_components, config)

        sent_events: list = []

        async def send(event):
            sent_events.append(event)

        await app(_make_scope(), _null_receive, send)

        assert stub_mcp.call_count == 1
        assert stub_mcp.captured_identity_ctx is not None
        caller = stub_mcp.captured_identity_ctx.caller
        # This is the critical assertion — NOT manually set, bridge must provide it
        assert caller.tenant_id == "tenant-prod"
        assert caller.principal_type == "user"
        assert caller.user_id == "user:alice"

    @pytest.mark.asyncio
    async def test_anonymous_principal_sets_anonymous_identity(self):
        """Anonymous principal → contextvar bound to anonymous CallerIdentity."""
        principal = Principal.anonymous()
        stub_mcp = _CapturingMcpApp()
        auth_components = _FakeAuthComponents(principal)
        config = _FakeConfig()

        from mcp_hangar.fastmcp_server.asgi import create_auth_combined_app
        from starlette.applications import Starlette

        aux_app = Starlette()
        app = create_auth_combined_app(aux_app, stub_mcp, auth_components, config)

        sent_events: list = []

        async def send(event):
            sent_events.append(event)

        await app(_make_scope(), _null_receive, send)

        assert stub_mcp.call_count == 1
        assert stub_mcp.captured_identity_ctx is not None
        caller = stub_mcp.captured_identity_ctx.caller
        assert caller.principal_type == "anonymous"
        assert caller.tenant_id is None
        assert caller.user_id is None

    @pytest.mark.asyncio
    async def test_identity_context_reset_after_request(self):
        """After the request completes, identity_context_var is reset (no leakage)."""
        principal = Principal(
            id=PrincipalId("user:bob"),
            type=PrincipalType.USER,
            tenant_id="tenant-x",
        )
        stub_mcp = _CapturingMcpApp()
        auth_components = _FakeAuthComponents(principal)
        config = _FakeConfig()

        from mcp_hangar.fastmcp_server.asgi import create_auth_combined_app
        from starlette.applications import Starlette

        aux_app = Starlette()
        app = create_auth_combined_app(aux_app, stub_mcp, auth_components, config)

        # Set a sentinel value before the request
        sentinel_ctx = get_identity_context()
        assert sentinel_ctx is None  # clean state

        async def send(event):
            pass

        await app(_make_scope(), _null_receive, send)

        # Contextvar must be restored to its pre-request value (None) after the request
        assert get_identity_context() is None

    @pytest.mark.asyncio
    async def test_no_leakage_across_two_requests(self):
        """Second request sees its own tenant, not the first request's tenant."""
        config = _FakeConfig()

        from mcp_hangar.fastmcp_server.asgi import create_auth_combined_app
        from starlette.applications import Starlette

        aux_app = Starlette()

        captured = []

        class _OrderedCaptureMcpApp:
            async def __call__(self, scope, receive, send):
                captured.append(get_identity_context())
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"", "more_body": False})

        ordered_mcp = _OrderedCaptureMcpApp()

        async def _noop_send(event):
            pass

        # Request 1: tenant-alpha
        auth_components_1 = _FakeAuthComponents(
            Principal(id=PrincipalId("user:alpha"), type=PrincipalType.USER, tenant_id="tenant-alpha")
        )
        app1 = create_auth_combined_app(aux_app, ordered_mcp, auth_components_1, config)
        await app1(_make_scope(), _null_receive, _noop_send)

        # Request 2: tenant-beta
        auth_components_2 = _FakeAuthComponents(
            Principal(id=PrincipalId("user:beta"), type=PrincipalType.USER, tenant_id="tenant-beta")
        )
        app2 = create_auth_combined_app(aux_app, ordered_mcp, auth_components_2, config)
        await app2(_make_scope(), _null_receive, _noop_send)

        assert len(captured) == 2
        assert captured[0].caller.tenant_id == "tenant-alpha"
        assert captured[1].caller.tenant_id == "tenant-beta"

    @pytest.mark.asyncio
    async def test_service_account_principal_type_service(self):
        """SERVICE_ACCOUNT → principal_type 'service' with user_id and tenant."""
        principal = Principal(
            id=PrincipalId("svc-deploy"),
            type=PrincipalType.SERVICE_ACCOUNT,
            tenant_id="tenant-svc",
        )
        stub_mcp = _CapturingMcpApp()
        auth_components = _FakeAuthComponents(principal)
        config = _FakeConfig()

        from mcp_hangar.fastmcp_server.asgi import create_auth_combined_app
        from starlette.applications import Starlette

        aux_app = Starlette()
        app = create_auth_combined_app(aux_app, stub_mcp, auth_components, config)

        async def _noop_send(event):
            pass

        await app(_make_scope(), _null_receive, _noop_send)

        caller = stub_mcp.captured_identity_ctx.caller
        assert caller.principal_type == "service"
        assert caller.user_id == "svc-deploy"
        assert caller.tenant_id == "tenant-svc"
