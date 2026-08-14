"""The authentication/authorization middleware pair in `auth.infrastructure`."""

from dataclasses import FrozenInstanceError
from unittest.mock import Mock

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


class TestAuthContext:
    """Tests for the AuthContext frozen dataclass."""

    def test_is_authenticated_returns_true_for_real_user(self):
        from mcp_hangar.auth.infrastructure.middleware import AuthContext

        principal = _make_principal("user:bob")
        ctx = AuthContext(principal=principal, auth_method="jwt")
        assert ctx.is_authenticated() is True

    def test_is_authenticated_returns_false_for_anonymous(self):
        from mcp_hangar.auth.infrastructure.middleware import AuthContext

        ctx = AuthContext(principal=Principal.anonymous(), auth_method="anonymous")
        assert ctx.is_authenticated() is False

    def test_auth_context_is_frozen(self):
        from mcp_hangar.auth.infrastructure.middleware import AuthContext

        ctx = AuthContext(principal=_make_principal(), auth_method="api_key")
        with pytest.raises((AttributeError, FrozenInstanceError)):
            ctx.auth_method = "something_else"


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
        from mcp_hangar.auth.infrastructure.middleware import AuthenticationMiddleware

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


class TestAuthorizationMiddleware:
    """Tests for the authorization middleware."""

    def _make_authorizer(self, allowed: bool = True, role: str = "admin", reason: str = ""):
        authz = Mock(spec=IAuthorizer)
        result = AuthorizationResult(allowed=allowed, matched_role=role, reason=reason)
        authz.authorize.return_value = result
        return authz

    def _make_middleware(self, authorizer=None, event_publisher=None):
        from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware

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


class TestCreateAuthRequestFromHeaders:
    """Tests for the create_auth_request_from_headers helper."""

    def test_creates_auth_request_with_normalized_headers(self):
        from mcp_hangar.auth.infrastructure.middleware import create_auth_request_from_headers

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
        from mcp_hangar.auth.infrastructure.middleware import create_auth_request_from_headers

        req = create_auth_request_from_headers(headers={})
        assert req.source_ip == "unknown"
        assert req.method == ""
        assert req.path == ""
