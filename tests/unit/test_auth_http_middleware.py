"""`AuthMiddlewareHTTP`: which requests it lets through and what it attaches to them."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mcp_hangar.domain.exceptions import (
    AccessDeniedError,
    AuthenticationError,
    MissingCredentialsError,
)
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType


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
        from mcp_hangar.auth.infrastructure.middleware import AuthContext

        authn = Mock()
        if error:
            authn.authenticate.side_effect = error
        else:
            ctx = auth_context or AuthContext(principal=_make_principal(), auth_method="api_key")
            authn.authenticate.return_value = ctx
        return authn

    @pytest.mark.asyncio
    async def test_skip_paths_bypass_authentication(self):
        from mcp_hangar.auth.http_middleware import AuthMiddlewareHTTP

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
        from mcp_hangar.auth.http_middleware import AuthMiddlewareHTTP

        authn = self._make_authn_middleware()
        call_next = AsyncMock(return_value=Mock(status_code=200))
        app = Mock()
        mw = AuthMiddlewareHTTP(app, authn=authn, skip_paths=["/custom-health"])

        request = self._make_request(path="/custom-health")
        await mw.dispatch(request, call_next)

        authn.authenticate.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_skip_paths_include_ready_and_metrics(self):
        from mcp_hangar.auth.http_middleware import AuthMiddlewareHTTP

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
        from mcp_hangar.auth.http_middleware import AuthMiddlewareHTTP
        from mcp_hangar.auth.infrastructure.middleware import AuthContext

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
        from mcp_hangar.auth.http_middleware import AuthMiddlewareHTTP

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
        from mcp_hangar.auth.http_middleware import AuthMiddlewareHTTP

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
        from mcp_hangar.auth.http_middleware import AuthMiddlewareHTTP
        from mcp_hangar.auth.infrastructure.middleware import AuthContext

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
        from mcp_hangar.auth.http_middleware import AuthMiddlewareHTTP
        from mcp_hangar.auth.infrastructure.middleware import AuthContext

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
        from mcp_hangar.auth.http_middleware import AuthMiddlewareHTTP
        from mcp_hangar.auth.infrastructure.middleware import AuthContext
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
        from mcp_hangar.auth.http_middleware import AuthMiddlewareHTTP
        from mcp_hangar.auth.infrastructure.middleware import AuthContext

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


class TestGetPrincipalFromRequest:
    """Tests for the get_principal_from_request helper."""

    def test_returns_principal_when_auth_context_present(self):
        from mcp_hangar.auth.http_middleware import get_principal_from_request
        from mcp_hangar.auth.infrastructure.middleware import AuthContext

        principal = _make_principal("user:alice")
        request = Mock()
        request.state = SimpleNamespace(auth=AuthContext(principal=principal, auth_method="jwt"))

        result = get_principal_from_request(request)
        assert result is principal

    def test_returns_none_when_no_auth_context(self):
        from mcp_hangar.auth.http_middleware import get_principal_from_request

        request = Mock()
        request.state = SimpleNamespace()  # No 'auth' attribute

        result = get_principal_from_request(request)
        assert result is None


class TestRequireAuth:
    """Tests for the require_auth helper."""

    def test_returns_principal_when_authenticated(self):
        from mcp_hangar.auth.http_middleware import require_auth
        from mcp_hangar.auth.infrastructure.middleware import AuthContext

        principal = _make_principal("user:bob")
        request = Mock()
        request.state = SimpleNamespace(auth=AuthContext(principal=principal, auth_method="api_key"))

        result = require_auth(request)
        assert result is principal

    def test_raises_missing_credentials_when_no_auth(self):
        from mcp_hangar.auth.http_middleware import require_auth

        request = Mock()
        request.state = SimpleNamespace()

        with pytest.raises(MissingCredentialsError, match="Authentication required"):
            require_auth(request)

    def test_raises_missing_credentials_when_anonymous(self):
        from mcp_hangar.auth.http_middleware import require_auth
        from mcp_hangar.auth.infrastructure.middleware import AuthContext

        request = Mock()
        request.state = SimpleNamespace(auth=AuthContext(principal=Principal.anonymous(), auth_method="anonymous"))

        with pytest.raises(MissingCredentialsError, match="Authentication required"):
            require_auth(request)
