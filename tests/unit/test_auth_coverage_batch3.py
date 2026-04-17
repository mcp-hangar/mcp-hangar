"""Batch 3: HTTP middleware, API routes, identity middleware, enterprise auth middleware.

Covers:
- enterprise/auth/http_middleware.py (AuthMiddlewareHTTP, get_principal_from_request, require_auth)
- enterprise/auth/api/routes.py (17 async route handlers, auth_routes list)
- src/mcp_hangar/infrastructure/identity/middleware.py (IdentityMiddleware)
- enterprise/auth/infrastructure/middleware.py (AuthenticationMiddleware, AuthorizationMiddleware,
  AuthContext, create_auth_request_from_headers)
"""

import asyncio
import json
from dataclasses import FrozenInstanceError, dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from mcp_hangar.domain.contracts.authentication import AuthRequest, IAuthenticator
from mcp_hangar.domain.contracts.authorization import AuthorizationRequest, AuthorizationResult, IAuthorizer
from mcp_hangar.domain.exceptions import (
    AccessDeniedError,
    AuthenticationError,
    MissingCredentialsError,
    RateLimitExceededError,
)
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_principal(
    pid: str = "user:alice",
    ptype: PrincipalType = PrincipalType.USER,
    tenant_id: str | None = None,
    groups: frozenset[str] | None = None,
) -> Principal:
    return Principal(
        id=PrincipalId(pid),
        type=ptype,
        tenant_id=tenant_id,
        groups=groups or frozenset(),
    )


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ===========================================================================
# enterprise/auth/infrastructure/middleware.py -- AuthContext
# ===========================================================================


class TestAuthContext:
    """Tests for the AuthContext frozen dataclass."""

    def test_is_authenticated_returns_true_for_real_user(self):
        from enterprise.auth.infrastructure.middleware import AuthContext

        principal = _make_principal("user:bob")
        ctx = AuthContext(principal=principal, auth_method="jwt")
        assert ctx.is_authenticated() is True

    def test_is_authenticated_returns_false_for_anonymous(self):
        from enterprise.auth.infrastructure.middleware import AuthContext

        ctx = AuthContext(principal=Principal.anonymous(), auth_method="anonymous")
        assert ctx.is_authenticated() is False

    def test_auth_context_is_frozen(self):
        from enterprise.auth.infrastructure.middleware import AuthContext

        ctx = AuthContext(principal=_make_principal(), auth_method="api_key")
        with pytest.raises((AttributeError, FrozenInstanceError)):
            setattr(ctx, "auth_method", "something_else")


# ===========================================================================
# enterprise/auth/infrastructure/middleware.py -- AuthenticationMiddleware
# ===========================================================================


class TestAuthenticationMiddleware:
    """Tests for the chain-of-responsibility authentication middleware."""

    def _make_authenticator(self, supports: bool = True, principal: Principal | None = None):
        auth = Mock(spec=IAuthenticator)
        auth.supports.return_value = supports
        if principal:
            auth.authenticate.return_value = principal
        auth.__class__.__name__ = "MockAuthenticator"
        return auth

    def _make_middleware(self, authenticators=None, allow_anonymous=False, event_publisher=None, rate_limiter=None):
        from enterprise.auth.infrastructure.middleware import AuthenticationMiddleware

        return AuthenticationMiddleware(
            authenticators=authenticators or [],
            allow_anonymous=allow_anonymous,
            event_publisher=event_publisher,
            rate_limiter=rate_limiter,
        )

    def _auth_request(self, source_ip="10.0.0.1", path="/api/test"):
        return AuthRequest(headers={"authorization": "Bearer tok"}, source_ip=source_ip, method="GET", path=path)

    # --- successful auth ---

    def test_first_supporting_authenticator_handles_request(self):
        principal = _make_principal("user:alice")
        auth1 = self._make_authenticator(supports=False)
        auth2 = self._make_authenticator(supports=True, principal=principal)

        mw = self._make_middleware(authenticators=[auth1, auth2])
        ctx = mw.authenticate(self._auth_request())

        assert ctx.principal is principal
        assert ctx.auth_method == "MockAuthenticator"
        auth1.authenticate.assert_not_called()
        auth2.authenticate.assert_called_once()

    def test_authentication_success_publishes_event(self):
        publisher = Mock()
        principal = _make_principal("user:alice")
        auth = self._make_authenticator(principal=principal)
        mw = self._make_middleware(authenticators=[auth], event_publisher=publisher)

        mw.authenticate(self._auth_request())

        publisher.assert_called_once()
        event = publisher.call_args[0][0]
        from mcp_hangar.domain.events import AuthenticationSucceeded

        assert isinstance(event, AuthenticationSucceeded)
        assert event.principal_id == "user:alice"

    def test_authentication_success_records_success_on_rate_limiter(self):
        rl = Mock()
        rl.check_rate_limit.return_value = Mock(allowed=True)
        principal = _make_principal()
        auth = self._make_authenticator(principal=principal)
        mw = self._make_middleware(authenticators=[auth], rate_limiter=rl)

        mw.authenticate(self._auth_request(source_ip="1.2.3.4"))

        rl.record_success.assert_called_once_with("1.2.3.4")

    # --- anonymous handling ---

    def test_no_authenticator_matched_allows_anonymous_when_configured(self):
        auth = self._make_authenticator(supports=False)
        mw = self._make_middleware(authenticators=[auth], allow_anonymous=True)

        ctx = mw.authenticate(self._auth_request())
        assert ctx.principal.is_anonymous()
        assert ctx.auth_method == "anonymous"

    def test_no_authenticator_matched_raises_missing_credentials_when_anonymous_disallowed(self):
        auth = self._make_authenticator(supports=False)
        mw = self._make_middleware(authenticators=[auth], allow_anonymous=False)

        with pytest.raises(MissingCredentialsError) as exc_info:
            mw.authenticate(self._auth_request())
        assert "MockAuthenticator" in exc_info.value.expected_methods

    def test_no_authenticators_at_all_and_anonymous_allowed(self):
        mw = self._make_middleware(authenticators=[], allow_anonymous=True)
        ctx = mw.authenticate(self._auth_request())
        assert ctx.principal.is_anonymous()

    def test_no_authenticators_at_all_and_anonymous_disallowed(self):
        mw = self._make_middleware(authenticators=[], allow_anonymous=False)
        with pytest.raises(MissingCredentialsError):
            mw.authenticate(self._auth_request())

    # --- authentication failure ---

    def test_authentication_failure_re_raises_error(self):
        auth = Mock(spec=IAuthenticator)
        auth.supports.return_value = True
        auth.authenticate.side_effect = AuthenticationError("bad token")
        auth.__class__.__name__ = "JWTAuthenticator"

        mw = self._make_middleware(authenticators=[auth])
        with pytest.raises(AuthenticationError, match="bad token"):
            mw.authenticate(self._auth_request())

    def test_authentication_failure_records_failure_on_rate_limiter(self):
        rl = Mock()
        rl.check_rate_limit.return_value = Mock(allowed=True)
        auth = Mock(spec=IAuthenticator)
        auth.supports.return_value = True
        auth.authenticate.side_effect = AuthenticationError("nope")
        auth.__class__.__name__ = "X"

        mw = self._make_middleware(authenticators=[auth], rate_limiter=rl)

        with pytest.raises(AuthenticationError):
            mw.authenticate(self._auth_request(source_ip="5.5.5.5"))

        rl.record_failure.assert_called_once_with("5.5.5.5")

    def test_authentication_failure_publishes_failed_event(self):
        publisher = Mock()
        auth = Mock(spec=IAuthenticator)
        auth.supports.return_value = True
        auth.authenticate.side_effect = AuthenticationError("nope")
        auth.__class__.__name__ = "JWTAuth"

        mw = self._make_middleware(authenticators=[auth], event_publisher=publisher)
        with pytest.raises(AuthenticationError):
            mw.authenticate(self._auth_request())

        publisher.assert_called_once()
        event = publisher.call_args[0][0]
        from mcp_hangar.domain.events import AuthenticationFailed

        assert isinstance(event, AuthenticationFailed)

    # --- rate limiting ---

    def test_rate_limit_exceeded_raises_error(self):
        rl = Mock()
        rl.check_rate_limit.return_value = Mock(allowed=False, reason="too many", retry_after=60.0)
        mw = self._make_middleware(authenticators=[], rate_limiter=rl)

        with pytest.raises(RateLimitExceededError):
            mw.authenticate(self._auth_request())

    def test_rate_limit_not_configured_skips_check(self):
        auth = self._make_authenticator(principal=_make_principal())
        mw = self._make_middleware(authenticators=[auth], rate_limiter=None)
        # Should not raise
        ctx = mw.authenticate(self._auth_request())
        assert ctx.is_authenticated()

    # --- event publisher fault barrier ---

    def test_event_publisher_failure_does_not_break_auth(self):
        publisher = Mock(side_effect=RuntimeError("event bus down"))
        principal = _make_principal()
        auth = self._make_authenticator(principal=principal)
        mw = self._make_middleware(authenticators=[auth], event_publisher=publisher)

        # Should succeed despite event publishing failure
        ctx = mw.authenticate(self._auth_request())
        assert ctx.is_authenticated()


# ===========================================================================
# enterprise/auth/infrastructure/middleware.py -- AuthorizationMiddleware
# ===========================================================================


class TestAuthorizationMiddleware:
    """Tests for the authorization middleware."""

    def _make_authorizer(self, allowed: bool = True, role: str = "admin", reason: str = ""):
        authz = Mock(spec=IAuthorizer)
        result = AuthorizationResult(allowed=allowed, matched_role=role, reason=reason)
        authz.authorize.return_value = result
        return authz

    def _make_middleware(self, authorizer=None, event_publisher=None):
        from enterprise.auth.infrastructure.middleware import AuthorizationMiddleware

        return AuthorizationMiddleware(
            authorizer=authorizer or self._make_authorizer(),
            event_publisher=event_publisher,
        )

    def test_authorize_succeeds_when_allowed(self):
        mw = self._make_middleware(authorizer=self._make_authorizer(allowed=True))
        principal = _make_principal()
        # Should not raise
        mw.authorize(principal, "read", "provider", "math")

    def test_authorize_raises_access_denied_when_not_allowed(self):
        mw = self._make_middleware(authorizer=self._make_authorizer(allowed=False, reason="no perms"))
        principal = _make_principal("user:bob")

        with pytest.raises(AccessDeniedError) as exc_info:
            mw.authorize(principal, "write", "config", "main")
        assert "user:bob" in str(exc_info.value)
        assert exc_info.value.action == "write"

    def test_check_returns_true_when_allowed(self):
        mw = self._make_middleware(authorizer=self._make_authorizer(allowed=True))
        assert mw.check(_make_principal(), "read", "provider", "*") is True

    def test_check_returns_false_when_denied(self):
        mw = self._make_middleware(authorizer=self._make_authorizer(allowed=False))
        assert mw.check(_make_principal(), "read", "provider", "*") is False

    def test_authorize_publishes_granted_event(self):
        publisher = Mock()
        mw = self._make_middleware(
            authorizer=self._make_authorizer(allowed=True, role="admin"),
            event_publisher=publisher,
        )
        mw.authorize(_make_principal("user:alice"), "invoke", "tool", "calculator")

        publisher.assert_called_once()
        event = publisher.call_args[0][0]
        from mcp_hangar.domain.events import AuthorizationGranted

        assert isinstance(event, AuthorizationGranted)
        assert event.principal_id == "user:alice"
        assert event.granted_by_role == "admin"

    def test_authorize_publishes_denied_event(self):
        publisher = Mock()
        mw = self._make_middleware(
            authorizer=self._make_authorizer(allowed=False, reason="nope"),
            event_publisher=publisher,
        )
        with pytest.raises(AccessDeniedError):
            mw.authorize(_make_principal("user:bob"), "delete", "provider", "x")

        publisher.assert_called_once()
        event = publisher.call_args[0][0]
        from mcp_hangar.domain.events import AuthorizationDenied

        assert isinstance(event, AuthorizationDenied)
        assert event.reason == "nope"

    def test_authorize_with_context_passes_to_authorizer(self):
        authz = self._make_authorizer(allowed=True)
        mw = self._make_middleware(authorizer=authz)
        mw.authorize(_make_principal(), "read", "provider", "*", context={"tenant": "acme"})

        call_args = authz.authorize.call_args[0][0]
        assert isinstance(call_args, AuthorizationRequest)
        assert call_args.context == {"tenant": "acme"}

    def test_event_publisher_failure_does_not_break_authorization(self):
        publisher = Mock(side_effect=RuntimeError("boom"))
        mw = self._make_middleware(
            authorizer=self._make_authorizer(allowed=True),
            event_publisher=publisher,
        )
        # Should not raise
        mw.authorize(_make_principal(), "read", "provider", "*")


# ===========================================================================
# enterprise/auth/infrastructure/middleware.py -- create_auth_request_from_headers
# ===========================================================================


class TestCreateAuthRequestFromHeaders:
    """Tests for the create_auth_request_from_headers helper."""

    def test_creates_auth_request_with_normalized_headers(self):
        from enterprise.auth.infrastructure.middleware import create_auth_request_from_headers

        req = create_auth_request_from_headers(
            headers={"Authorization": "Bearer abc", "X-Custom": "val"},
            source_ip="192.168.1.1",
            method="POST",
            path="/api/keys",
        )
        assert isinstance(req, AuthRequest)
        assert req.source_ip == "192.168.1.1"
        assert req.method == "POST"
        assert req.path == "/api/keys"
        # Both lowercase and original case should be present
        assert req.headers.get("authorization") == "Bearer abc"
        assert req.headers.get("Authorization") == "Bearer abc"

    def test_defaults_for_optional_params(self):
        from enterprise.auth.infrastructure.middleware import create_auth_request_from_headers

        req = create_auth_request_from_headers(headers={})
        assert req.source_ip == "unknown"
        assert req.method == ""
        assert req.path == ""


# ===========================================================================
# enterprise/auth/http_middleware.py -- AuthMiddlewareHTTP
# ===========================================================================


class TestAuthMiddlewareHTTP:
    """Tests for the Starlette HTTP authentication middleware."""

    def _make_request(self, path="/api/test", method="GET", headers=None, client_host="10.0.0.1"):
        """Create a mock Starlette Request."""
        req = Mock()
        req.url = Mock()
        req.url.path = path
        req.method = method
        req.headers = headers or {}
        req.client = Mock()
        req.client.host = client_host
        req.state = SimpleNamespace()
        return req

    def _make_authn_middleware(self, auth_context=None, error=None):
        from enterprise.auth.infrastructure.middleware import AuthContext

        authn = Mock()
        if error:
            authn.authenticate.side_effect = error
        else:
            ctx = auth_context or AuthContext(principal=_make_principal(), auth_method="api_key")
            authn.authenticate.return_value = ctx
        return authn

    @pytest.mark.asyncio
    async def test_skip_paths_bypass_authentication(self):
        from enterprise.auth.http_middleware import AuthMiddlewareHTTP

        authn = self._make_authn_middleware()
        call_next = AsyncMock(return_value=Mock(status_code=200))

        app = Mock()
        mw = AuthMiddlewareHTTP(app, authn=authn)
        request = self._make_request(path="/health")

        await mw.dispatch(request, call_next)

        authn.authenticate.assert_not_called()
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_custom_skip_paths(self):
        from enterprise.auth.http_middleware import AuthMiddlewareHTTP

        authn = self._make_authn_middleware()
        call_next = AsyncMock(return_value=Mock(status_code=200))
        app = Mock()
        mw = AuthMiddlewareHTTP(app, authn=authn, skip_paths=["/custom-health"])

        request = self._make_request(path="/custom-health")
        await mw.dispatch(request, call_next)

        authn.authenticate.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_skip_paths_include_ready_and_metrics(self):
        from enterprise.auth.http_middleware import AuthMiddlewareHTTP

        authn = self._make_authn_middleware()
        call_next = AsyncMock(return_value=Mock(status_code=200))
        app = Mock()
        mw = AuthMiddlewareHTTP(app, authn=authn)

        for path in ["/health", "/ready", "/_ready", "/metrics"]:
            request = self._make_request(path=path)
            await mw.dispatch(request, call_next)

        authn.authenticate.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_auth_attaches_context_to_request_state(self):
        from enterprise.auth.http_middleware import AuthMiddlewareHTTP
        from enterprise.auth.infrastructure.middleware import AuthContext
        from mcp_hangar.infrastructure.identity import TrustedProxyResolver

        principal = _make_principal("user:alice")
        auth_ctx = AuthContext(principal=principal, auth_method="jwt")
        authn = self._make_authn_middleware(auth_context=auth_ctx)
        call_next = AsyncMock(return_value=Mock(status_code=200))

        app = Mock()
        mw = AuthMiddlewareHTTP(app, authn=authn)
        request = self._make_request(path="/api/test")

        await mw.dispatch(request, call_next)

        assert request.state.auth is auth_ctx
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_authentication_error_returns_401(self):
        from enterprise.auth.http_middleware import AuthMiddlewareHTTP

        authn = self._make_authn_middleware(error=AuthenticationError("bad token"))
        call_next = AsyncMock()

        app = Mock()
        mw = AuthMiddlewareHTTP(app, authn=authn)
        request = self._make_request(path="/api/test")

        response = await mw.dispatch(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_called()
        body = json.loads(bytes(response.body))
        assert body["error"] == "authentication_failed"
        assert "WWW-Authenticate" in response.headers

    @pytest.mark.asyncio
    async def test_access_denied_error_returns_403(self):
        from enterprise.auth.http_middleware import AuthMiddlewareHTTP

        error = AccessDeniedError(principal_id="user:bob", action="write", resource="config:main")
        authn = self._make_authn_middleware(error=error)
        call_next = AsyncMock()

        app = Mock()
        mw = AuthMiddlewareHTTP(app, authn=authn)
        request = self._make_request(path="/api/config")

        response = await mw.dispatch(request, call_next)

        assert response.status_code == 403
        body = json.loads(bytes(response.body))
        assert body["error"] == "access_denied"
        assert body["principal_id"] == "user:bob"
        assert body["action"] == "write"

    @pytest.mark.asyncio
    async def test_build_auth_request_uses_client_ip(self):
        from enterprise.auth.http_middleware import AuthMiddlewareHTTP
        from enterprise.auth.infrastructure.middleware import AuthContext

        authn = Mock()
        captured_request = []

        def capture_authenticate(req):
            captured_request.append(req)
            return AuthContext(principal=_make_principal(), auth_method="test")

        authn.authenticate = capture_authenticate
        call_next = AsyncMock(return_value=Mock(status_code=200))

        app = Mock()
        mw = AuthMiddlewareHTTP(app, authn=authn)
        request = self._make_request(path="/api/test", client_host="192.168.1.100")

        await mw.dispatch(request, call_next)

        assert len(captured_request) == 1
        assert captured_request[0].source_ip == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_build_auth_request_unknown_ip_when_no_client(self):
        from enterprise.auth.http_middleware import AuthMiddlewareHTTP
        from enterprise.auth.infrastructure.middleware import AuthContext

        authn = Mock()
        captured_request = []

        def capture_authenticate(req):
            captured_request.append(req)
            return AuthContext(principal=_make_principal(), auth_method="test")

        authn.authenticate = capture_authenticate
        call_next = AsyncMock(return_value=Mock(status_code=200))

        app = Mock()
        mw = AuthMiddlewareHTTP(app, authn=authn)
        request = self._make_request(path="/api/test")
        request.client = None

        await mw.dispatch(request, call_next)

        assert captured_request[0].source_ip == "unknown"

    @pytest.mark.asyncio
    async def test_trusted_proxy_x_forwarded_for(self):
        from enterprise.auth.http_middleware import AuthMiddlewareHTTP
        from enterprise.auth.infrastructure.middleware import AuthContext
        from mcp_hangar.infrastructure.identity import TrustedProxyResolver

        authn = Mock()
        captured_request = []

        def capture_authenticate(req):
            captured_request.append(req)
            return AuthContext(principal=_make_principal(), auth_method="test")

        authn.authenticate = capture_authenticate
        call_next = AsyncMock(return_value=Mock(status_code=200))

        app = Mock()
        mw = AuthMiddlewareHTTP(app, authn=authn)
        mw._trusted_proxies = TrustedProxyResolver(frozenset({"10.0.0.1"}))
        request = self._make_request(
            path="/api/test",
            client_host="10.0.0.1",
            headers={"x-forwarded-for": "203.0.113.50, 70.41.3.18"},
        )

        await mw.dispatch(request, call_next)

        assert captured_request[0].source_ip == "203.0.113.50"

    @pytest.mark.asyncio
    async def test_untrusted_proxy_ignores_x_forwarded_for(self):
        from enterprise.auth.http_middleware import AuthMiddlewareHTTP
        from enterprise.auth.infrastructure.middleware import AuthContext

        authn = Mock()
        captured_request = []

        def capture_authenticate(req):
            captured_request.append(req)
            return AuthContext(principal=_make_principal(), auth_method="test")

        authn.authenticate = capture_authenticate
        call_next = AsyncMock(return_value=Mock(status_code=200))

        app = Mock()
        mw = AuthMiddlewareHTTP(app, authn=authn)
        # _trusted_proxies is empty by default
        request = self._make_request(
            path="/api/test",
            client_host="10.0.0.2",
            headers={"x-forwarded-for": "203.0.113.50"},
        )

        await mw.dispatch(request, call_next)

        # Should NOT use x-forwarded-for
        assert captured_request[0].source_ip == "10.0.0.2"


# ===========================================================================
# enterprise/auth/http_middleware.py -- get_principal_from_request / require_auth
# ===========================================================================


class TestGetPrincipalFromRequest:
    """Tests for the get_principal_from_request helper."""

    def test_returns_principal_when_auth_context_present(self):
        from enterprise.auth.http_middleware import get_principal_from_request
        from enterprise.auth.infrastructure.middleware import AuthContext

        principal = _make_principal("user:alice")
        request = Mock()
        request.state = SimpleNamespace(auth=AuthContext(principal=principal, auth_method="jwt"))

        result = get_principal_from_request(request)
        assert result is principal

    def test_returns_none_when_no_auth_context(self):
        from enterprise.auth.http_middleware import get_principal_from_request

        request = Mock()
        request.state = SimpleNamespace()  # No 'auth' attribute

        result = get_principal_from_request(request)
        assert result is None


class TestRequireAuth:
    """Tests for the require_auth helper."""

    def test_returns_principal_when_authenticated(self):
        from enterprise.auth.http_middleware import require_auth
        from enterprise.auth.infrastructure.middleware import AuthContext

        principal = _make_principal("user:bob")
        request = Mock()
        request.state = SimpleNamespace(auth=AuthContext(principal=principal, auth_method="api_key"))

        result = require_auth(request)
        assert result is principal

    def test_raises_missing_credentials_when_no_auth(self):
        from enterprise.auth.http_middleware import require_auth

        request = Mock()
        request.state = SimpleNamespace()

        with pytest.raises(MissingCredentialsError, match="Authentication required"):
            require_auth(request)

    def test_raises_missing_credentials_when_anonymous(self):
        from enterprise.auth.http_middleware import require_auth
        from enterprise.auth.infrastructure.middleware import AuthContext

        request = Mock()
        request.state = SimpleNamespace(auth=AuthContext(principal=Principal.anonymous(), auth_method="anonymous"))

        with pytest.raises(MissingCredentialsError, match="Authentication required"):
            require_auth(request)


# ===========================================================================
# src/mcp_hangar/infrastructure/identity/middleware.py -- IdentityMiddleware
# ===========================================================================


class TestIdentityMiddleware:
    """Tests for the ASGI identity middleware."""

    def _make_extractor(self, identity=None):
        extractor = Mock()
        extractor.extract.return_value = identity
        return extractor

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self):
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware

        app = AsyncMock()
        extractor = self._make_extractor()
        mw = IdentityMiddleware(app=app, extractor=extractor)

        scope = {"type": "lifespan"}
        receive = AsyncMock()
        send = AsyncMock()

        await mw(scope, receive, send)

        app.assert_called_once_with(scope, receive, send)
        extractor.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_http_scope_extracts_identity_and_sets_context(self):
        from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware
        from mcp_hangar.context import identity_context_var

        identity = IdentityContext(
            caller=CallerIdentity(user_id="alice", agent_id="agent-1", session_id="s1", principal_type="user"),
            correlation_id="corr-1",
        )
        extractor = self._make_extractor(identity=identity)

        captured_identity = []

        async def inner_app(scope, receive, send):
            captured_identity.append(identity_context_var.get())

        mw = IdentityMiddleware(app=inner_app, extractor=extractor)

        scope = {
            "type": "http",
            "headers": [
                (b"x-user-id", b"alice"),
                (b"x-agent-id", b"agent-1"),
            ],
        }
        receive = AsyncMock()
        send = AsyncMock()

        await mw(scope, receive, send)

        assert len(captured_identity) == 1
        assert captured_identity[0] is identity

    @pytest.mark.asyncio
    async def test_context_is_reset_after_request(self):
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware
        from mcp_hangar.context import identity_context_var
        from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext

        identity = IdentityContext(
            caller=CallerIdentity(user_id="alice", agent_id=None, session_id=None, principal_type="user"),
        )
        extractor = self._make_extractor(identity=identity)

        async def inner_app(scope, receive, send):
            pass

        mw = IdentityMiddleware(app=inner_app, extractor=extractor)
        scope = {"type": "http", "headers": []}
        await mw(scope, AsyncMock(), AsyncMock())

        # After middleware completes, contextvar should be reset
        assert identity_context_var.get() is None

    @pytest.mark.asyncio
    async def test_context_is_reset_even_on_error(self):
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware
        from mcp_hangar.context import identity_context_var
        from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext

        identity = IdentityContext(
            caller=CallerIdentity(user_id="bob", agent_id=None, session_id=None, principal_type="user"),
        )
        extractor = self._make_extractor(identity=identity)

        async def inner_app(scope, receive, send):
            raise RuntimeError("app error")

        mw = IdentityMiddleware(app=inner_app, extractor=extractor)
        scope = {"type": "http", "headers": []}

        with pytest.raises(RuntimeError, match="app error"):
            await mw(scope, AsyncMock(), AsyncMock())

        assert identity_context_var.get() is None

    @pytest.mark.asyncio
    async def test_websocket_scope_also_extracts_identity(self):
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware
        from mcp_hangar.context import identity_context_var
        from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext

        identity = IdentityContext(
            caller=CallerIdentity(user_id="ws-user", agent_id=None, session_id=None, principal_type="user"),
        )
        extractor = self._make_extractor(identity=identity)

        captured = []

        async def inner_app(scope, receive, send):
            captured.append(identity_context_var.get())

        mw = IdentityMiddleware(app=inner_app, extractor=extractor)
        scope = {"type": "websocket", "headers": []}

        await mw(scope, AsyncMock(), AsyncMock())

        assert len(captured) == 1
        assert captured[0] is identity

    @pytest.mark.asyncio
    async def test_none_identity_still_sets_contextvar(self):
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware
        from mcp_hangar.context import identity_context_var

        extractor = self._make_extractor(identity=None)

        captured = []

        async def inner_app(scope, receive, send):
            captured.append(identity_context_var.get())

        mw = IdentityMiddleware(app=inner_app, extractor=extractor)
        scope = {"type": "http", "headers": []}

        await mw(scope, AsyncMock(), AsyncMock())

        assert captured[0] is None

    @pytest.mark.asyncio
    async def test_headers_decoded_from_asgi_scope(self):
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware

        captured_headers = []

        def capture_extract(headers, source_ip=None):
            captured_headers.append((headers, source_ip))
            return None

        extractor = Mock()
        extractor.extract = capture_extract

        async def inner_app(scope, receive, send):
            pass

        mw = IdentityMiddleware(app=inner_app, extractor=extractor)
        scope = {
            "type": "http",
            "headers": [
                (b"content-type", b"application/json"),
                (b"authorization", b"Bearer xyz"),
            ],
        }

        await mw(scope, AsyncMock(), AsyncMock())

        assert len(captured_headers) == 1
        assert captured_headers[0][0]["content-type"] == "application/json"
        assert captured_headers[0][0]["authorization"] == "Bearer xyz"
        assert captured_headers[0][1] is None


# ===========================================================================
# enterprise/auth/api/routes.py -- Route handlers
# ===========================================================================


class TestAuthRoutes:
    """Tests for the auth API route handlers."""

    def _make_request(self, body=None, path_params=None, query_params=None):
        request = AsyncMock()
        request.json = AsyncMock(return_value=body or {})
        request.path_params = path_params or {}
        request.query_params = query_params or {}
        return request

    # --- auth_routes list ---

    def test_auth_routes_list_is_not_empty(self):
        from enterprise.auth.api.routes import auth_routes

        assert len(auth_routes) > 0

    def test_auth_routes_all_are_route_instances(self):
        from enterprise.auth.api.routes import auth_routes
        from starlette.routing import Route

        for route in auth_routes:
            assert isinstance(route, Route)

    def test_auth_routes_contains_key_endpoints(self):
        from enterprise.auth.api.routes import auth_routes

        paths = [r.path for r in auth_routes]
        assert "/keys" in paths
        assert "/roles" in paths
        assert "/principals" in paths
        assert "/permissions" in paths
        assert "/check-permission" in paths

    # --- create_api_key ---

    @pytest.mark.asyncio
    async def test_create_api_key_dispatches_command(self):
        from enterprise.auth.api.routes import create_api_key

        request = self._make_request(body={"principal_id": "user:alice", "name": "my-key"})

        with patch("enterprise.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"key_id": "k1", "raw_key": "secret"}
            response = await create_api_key(request)

        assert response.status_code == 201
        cmd = mock_dispatch.call_args[0][0]
        assert cmd.principal_id == "user:alice"
        assert cmd.name == "my-key"
        assert cmd.created_by == "system"

    @pytest.mark.asyncio
    async def test_create_api_key_with_expires_at(self):
        from enterprise.auth.api.routes import create_api_key

        request = self._make_request(
            body={
                "principal_id": "user:bob",
                "name": "temp-key",
                "expires_at": "2026-12-31T23:59:59+00:00",
            }
        )

        with patch("enterprise.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"key_id": "k2"}
            response = await create_api_key(request)

        cmd = mock_dispatch.call_args[0][0]
        assert cmd.expires_at is not None
        assert cmd.expires_at.year == 2026

    # --- revoke_api_key ---

    @pytest.mark.asyncio
    async def test_revoke_api_key_dispatches_command(self):
        from enterprise.auth.api.routes import revoke_api_key

        request = self._make_request(
            body={"revoked_by": "admin", "reason": "compromised"},
            path_params={"key_id": "k1"},
        )

        with patch("enterprise.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"revoked": True}
            response = await revoke_api_key(request)

        cmd = mock_dispatch.call_args[0][0]
        assert cmd.key_id == "k1"
        assert cmd.revoked_by == "admin"
        assert cmd.reason == "compromised"

    @pytest.mark.asyncio
    async def test_revoke_api_key_handles_empty_body(self):
        from enterprise.auth.api.routes import revoke_api_key

        request = AsyncMock()
        request.path_params = {"key_id": "k2"}
        request.json = AsyncMock(side_effect=json.JSONDecodeError("err", "", 0))

        with patch("enterprise.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"revoked": True}
            response = await revoke_api_key(request)

        cmd = mock_dispatch.call_args[0][0]
        assert cmd.revoked_by == "system"
        assert cmd.reason == ""

    # --- list_api_keys ---

    @pytest.mark.asyncio
    async def test_list_api_keys_dispatches_query(self):
        from enterprise.auth.api.routes import list_api_keys

        request = self._make_request(query_params={"principal_id": "user:alice"})

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"keys": [], "total": 0}
            response = await list_api_keys(request)

        query = mock_dispatch.call_args[0][0]
        assert query.principal_id == "user:alice"
        assert query.include_revoked is True

    @pytest.mark.asyncio
    async def test_list_api_keys_include_revoked_false(self):
        from enterprise.auth.api.routes import list_api_keys

        request = self._make_request(query_params={"principal_id": "u", "include_revoked": "false"})

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"keys": []}
            await list_api_keys(request)

        query = mock_dispatch.call_args[0][0]
        assert query.include_revoked is False

    # --- assign_role ---

    @pytest.mark.asyncio
    async def test_assign_role_dispatches_command(self):
        from enterprise.auth.api.routes import assign_role

        request = self._make_request(
            body={
                "principal_id": "user:alice",
                "role_name": "admin",
                "scope": "tenant:acme",
                "assigned_by": "superadmin",
            }
        )

        with patch("enterprise.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"assigned": True}
            response = await assign_role(request)

        cmd = mock_dispatch.call_args[0][0]
        assert cmd.principal_id == "user:alice"
        assert cmd.role_name == "admin"
        assert cmd.scope == "tenant:acme"
        assert cmd.assigned_by == "superadmin"

    # --- revoke_role ---

    @pytest.mark.asyncio
    async def test_revoke_role_dispatches_command(self):
        from enterprise.auth.api.routes import revoke_role

        request = self._make_request(
            body={
                "principal_id": "user:bob",
                "role_name": "viewer",
            }
        )

        with patch("enterprise.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"revoked": True}
            response = await revoke_role(request)

        cmd = mock_dispatch.call_args[0][0]
        assert cmd.principal_id == "user:bob"
        assert cmd.role_name == "viewer"
        assert cmd.scope == "global"

    # --- list_roles ---

    @pytest.mark.asyncio
    async def test_list_roles_dispatches_query(self):
        from enterprise.auth.api.routes import list_roles

        request = self._make_request()

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"roles": []}
            response = await list_roles(request)

        from enterprise.auth.queries.queries import ListBuiltinRolesQuery

        assert isinstance(mock_dispatch.call_args[0][0], ListBuiltinRolesQuery)

    # --- create_custom_role ---

    @pytest.mark.asyncio
    async def test_create_custom_role_dispatches_command(self):
        from enterprise.auth.api.routes import create_custom_role

        request = self._make_request(
            body={
                "role_name": "deployer",
                "description": "Can deploy",
                "permissions": ["provider:write:*"],
            }
        )

        with patch("enterprise.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"role_name": "deployer"}
            response = await create_custom_role(request)

        assert response.status_code == 201
        cmd = mock_dispatch.call_args[0][0]
        assert cmd.role_name == "deployer"
        assert "provider:write:*" in cmd.permissions

    # --- get_principal_roles ---

    @pytest.mark.asyncio
    async def test_get_principal_roles_dispatches_query(self):
        from enterprise.auth.api.routes import get_principal_roles

        request = self._make_request(query_params={"principal_id": "user:alice", "scope": "global"})

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"roles": []}
            response = await get_principal_roles(request)

        query = mock_dispatch.call_args[0][0]
        assert query.principal_id == "user:alice"
        assert query.scope == "global"

    # --- list_all_roles ---

    @pytest.mark.asyncio
    async def test_list_all_roles_dispatches_query(self):
        from enterprise.auth.api.routes import list_all_roles

        request = self._make_request(query_params={})

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"roles": [], "total": 0}
            response = await list_all_roles(request)

        query = mock_dispatch.call_args[0][0]
        assert query.include_builtin is True

    @pytest.mark.asyncio
    async def test_list_all_roles_exclude_builtin(self):
        from enterprise.auth.api.routes import list_all_roles

        request = self._make_request(query_params={"include_builtin": "false"})

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"roles": []}
            await list_all_roles(request)

        query = mock_dispatch.call_args[0][0]
        assert query.include_builtin is False

    # --- get_role ---

    @pytest.mark.asyncio
    async def test_get_role_found(self):
        from enterprise.auth.api.routes import get_role

        request = self._make_request(path_params={"role_name": "admin"})

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"found": True, "role_name": "admin"}
            response = await get_role(request)

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_role_not_found_returns_404(self):
        from enterprise.auth.api.routes import get_role

        request = self._make_request(path_params={"role_name": "nonexistent"})

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"found": False}
            response = await get_role(request)

        assert response.status_code == 404

    # --- delete_role ---

    @pytest.mark.asyncio
    async def test_delete_role_returns_204(self):
        from enterprise.auth.api.routes import delete_role

        request = self._make_request(path_params={"role_name": "custom-role"})

        with patch("enterprise.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = None
            response = await delete_role(request)

        assert response.status_code == 204

    # --- update_role ---

    @pytest.mark.asyncio
    async def test_update_role_dispatches_command(self):
        from enterprise.auth.api.routes import update_role

        request = self._make_request(
            path_params={"role_name": "deployer"},
            body={"permissions": ["provider:write:*"], "description": "Updated", "updated_by": "admin"},
        )

        with patch("enterprise.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"role_name": "deployer"}
            response = await update_role(request)

        cmd = mock_dispatch.call_args[0][0]
        assert cmd.role_name == "deployer"
        assert cmd.permissions == ["provider:write:*"]
        assert cmd.description == "Updated"

    # --- list_principals ---

    @pytest.mark.asyncio
    async def test_list_principals_dispatches_query(self):
        from enterprise.auth.api.routes import list_principals

        request = self._make_request()

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"principals": [], "total": 0}
            response = await list_principals(request)

        from enterprise.auth.queries.queries import ListPrincipalsQuery

        assert isinstance(mock_dispatch.call_args[0][0], ListPrincipalsQuery)

    # --- list_permissions ---

    @pytest.mark.asyncio
    async def test_list_permissions_returns_permission_manifest(self):
        from enterprise.auth.api.routes import list_permissions

        request = self._make_request()
        response = await list_permissions(request)

        body = json.loads(bytes(response.body))
        assert "permissions" in body
        assert len(body["permissions"]) > 0
        # Each entry should have resource_type and actions
        for perm in body["permissions"]:
            assert "resource_type" in perm
            assert "actions" in perm

    # --- check_permission ---

    @pytest.mark.asyncio
    async def test_check_permission_with_action_fields(self):
        from enterprise.auth.api.routes import check_permission

        request = self._make_request(
            body={
                "principal_id": "user:alice",
                "action": "invoke",
                "resource_type": "tool",
                "resource_id": "math",
            }
        )

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"allowed": True}
            response = await check_permission(request)

        query = mock_dispatch.call_args[0][0]
        assert query.principal_id == "user:alice"
        assert query.action == "invoke"
        assert query.resource_type == "tool"
        assert query.resource_id == "math"

    @pytest.mark.asyncio
    async def test_check_permission_with_combined_permission_string(self):
        from enterprise.auth.api.routes import check_permission

        request = self._make_request(
            body={
                "principal_id": "user:bob",
                "permission": "provider:read:math",
            }
        )

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"allowed": False}
            response = await check_permission(request)

        query = mock_dispatch.call_args[0][0]
        assert query.resource_type == "provider"
        assert query.action == "read"
        assert query.resource_id == "math"

    @pytest.mark.asyncio
    async def test_check_permission_with_partial_permission_string(self):
        from enterprise.auth.api.routes import check_permission

        request = self._make_request(
            body={
                "principal_id": "user:bob",
                "permission": "provider:read",
            }
        )

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"allowed": True}
            await check_permission(request)

        query = mock_dispatch.call_args[0][0]
        assert query.resource_type == "provider"
        assert query.action == "read"
        assert query.resource_id == "*"

    # --- set_tool_access_policy ---

    @pytest.mark.asyncio
    async def test_set_tool_access_policy_valid_scope(self):
        from enterprise.auth.api.routes import set_tool_access_policy

        request = self._make_request(
            path_params={"scope": "provider", "target_id": "math"},
            body={"allow_list": ["add", "subtract"], "deny_list": ["admin_*"]},
        )

        with patch("enterprise.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"status": "ok"}
            response = await set_tool_access_policy(request)

        assert response.status_code == 200
        cmd = mock_dispatch.call_args[0][0]
        assert cmd.scope == "provider"
        assert cmd.target_id == "math"
        assert cmd.allow_list == ["add", "subtract"]

    @pytest.mark.asyncio
    async def test_set_tool_access_policy_invalid_scope_returns_400(self):
        from enterprise.auth.api.routes import set_tool_access_policy

        request = self._make_request(
            path_params={"scope": "invalid", "target_id": "x"},
            body={},
        )

        response = await set_tool_access_policy(request)
        assert response.status_code == 400
        body = json.loads(bytes(response.body))
        assert "ValidationError" in body["error"]["code"]

    # --- get_tool_access_policy ---

    @pytest.mark.asyncio
    async def test_get_tool_access_policy_valid_scope(self):
        from enterprise.auth.api.routes import get_tool_access_policy

        request = self._make_request(path_params={"scope": "group", "target_id": "g1"})

        with patch("enterprise.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"allow_list": ["*"], "deny_list": []}
            response = await get_tool_access_policy(request)

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_tool_access_policy_invalid_scope_returns_400(self):
        from enterprise.auth.api.routes import get_tool_access_policy

        request = self._make_request(path_params={"scope": "bad", "target_id": "x"})

        response = await get_tool_access_policy(request)
        assert response.status_code == 400

    # --- clear_tool_access_policy ---

    @pytest.mark.asyncio
    async def test_clear_tool_access_policy_returns_204(self):
        from enterprise.auth.api.routes import clear_tool_access_policy

        request = self._make_request(path_params={"scope": "member", "target_id": "m1"})

        with patch("enterprise.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = None
            response = await clear_tool_access_policy(request)

        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_clear_tool_access_policy_invalid_scope_returns_400(self):
        from enterprise.auth.api.routes import clear_tool_access_policy

        request = self._make_request(path_params={"scope": "unknown", "target_id": "x"})

        response = await clear_tool_access_policy(request)
        assert response.status_code == 400

    # --- edge cases for TAP scopes ---

    @pytest.mark.asyncio
    async def test_all_valid_tap_scopes_accepted(self):
        from enterprise.auth.api.routes import set_tool_access_policy

        for scope in ("provider", "group", "member"):
            request = self._make_request(
                path_params={"scope": scope, "target_id": "t1"},
                body={"allow_list": ["*"]},
            )

            with patch("enterprise.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
                mock_dispatch.return_value = {"ok": True}
                response = await set_tool_access_policy(request)
                assert response.status_code == 200, f"Scope {scope} should be accepted"
