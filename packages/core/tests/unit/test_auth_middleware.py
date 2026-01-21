"""Tests for authentication and authorization middleware."""

from unittest.mock import Mock

import pytest

from mcp_hangar.domain.contracts.authentication import AuthRequest, IAuthenticator
from mcp_hangar.domain.contracts.authorization import AuthorizationResult, IAuthorizer
from mcp_hangar.domain.events import (
    AuthenticationFailed,
    AuthenticationSucceeded,
    AuthorizationDenied,
    AuthorizationGranted,
)
from mcp_hangar.domain.exceptions import (
    AccessDeniedError,
    AuthenticationError,
    MissingCredentialsError,
    RateLimitExceededError,
)
from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType
from mcp_hangar.infrastructure.auth.middleware import (
    AuthContext,
    AuthenticationMiddleware,
    AuthorizationMiddleware,
    create_auth_request_from_headers,
)
from mcp_hangar.infrastructure.auth.rate_limiter import RateLimitResult

# --- Fixtures ---


@pytest.fixture
def auth_request() -> AuthRequest:
    """Create a sample auth request."""
    return AuthRequest(
        headers={"authorization": "Bearer test-token"},
        source_ip="192.168.1.100",
        method="POST",
        path="/api/tools/invoke",
    )


@pytest.fixture
def sample_principal() -> Principal:
    """Create a sample authenticated principal."""
    return Principal(
        id=PrincipalId("user-123"),
        type=PrincipalType.USER,
        tenant_id="tenant-abc",
        metadata={"display_name": "Test User"},
    )


@pytest.fixture
def mock_authenticator(sample_principal: Principal) -> Mock:
    """Create a mock authenticator that succeeds."""
    authenticator = Mock(spec=IAuthenticator)
    authenticator.supports.return_value = True
    authenticator.authenticate.return_value = sample_principal
    authenticator.__class__.__name__ = "MockAuthenticator"
    return authenticator


@pytest.fixture
def mock_failing_authenticator() -> Mock:
    """Create a mock authenticator that fails."""
    authenticator = Mock(spec=IAuthenticator)
    authenticator.supports.return_value = True
    authenticator.authenticate.side_effect = AuthenticationError("Invalid credentials")
    authenticator.__class__.__name__ = "FailingAuthenticator"
    return authenticator


@pytest.fixture
def mock_unsupporting_authenticator() -> Mock:
    """Create a mock authenticator that doesn't support the request."""
    authenticator = Mock(spec=IAuthenticator)
    authenticator.supports.return_value = False
    authenticator.__class__.__name__ = "UnsupportingAuthenticator"
    return authenticator


@pytest.fixture
def mock_event_publisher() -> Mock:
    """Create a mock event publisher."""
    return Mock()


@pytest.fixture
def mock_rate_limiter() -> Mock:
    """Create a mock rate limiter that allows requests."""
    limiter = Mock()
    limiter.check_rate_limit.return_value = RateLimitResult(
        allowed=True,
        remaining=10,
        retry_after=None,
        reason="allowed",
    )
    return limiter


@pytest.fixture
def mock_blocking_rate_limiter() -> Mock:
    """Create a mock rate limiter that blocks requests."""
    limiter = Mock()
    limiter.check_rate_limit.return_value = RateLimitResult(
        allowed=False,
        remaining=0,
        retry_after=60.0,
        reason="Too many attempts",
    )
    return limiter


# --- AuthContext Tests ---


class TestAuthContext:
    """Tests for AuthContext dataclass."""

    def test_is_authenticated_returns_true_for_real_user(self, sample_principal: Principal):
        """Authenticated principal should return True."""
        context = AuthContext(principal=sample_principal, auth_method="Bearer")
        assert context.is_authenticated() is True

    def test_is_authenticated_returns_false_for_anonymous(self):
        """Anonymous principal should return False."""
        context = AuthContext(principal=Principal.anonymous(), auth_method="anonymous")
        assert context.is_authenticated() is False

    def test_auth_context_is_immutable(self, sample_principal: Principal):
        """AuthContext should be frozen (immutable)."""
        context = AuthContext(principal=sample_principal, auth_method="Bearer")
        with pytest.raises(AttributeError):
            context.auth_method = "different"


# --- AuthenticationMiddleware Tests ---


class TestAuthenticationMiddlewareSuccess:
    """Tests for successful authentication scenarios."""

    def test_authenticate_success_with_single_authenticator(
        self,
        auth_request: AuthRequest,
        mock_authenticator: Mock,
        sample_principal: Principal,
    ):
        """Should authenticate successfully with matching authenticator."""
        middleware = AuthenticationMiddleware(authenticators=[mock_authenticator])

        result = middleware.authenticate(auth_request)

        assert result.principal == sample_principal
        assert result.auth_method == "MockAuthenticator"
        mock_authenticator.supports.assert_called_once_with(auth_request)
        mock_authenticator.authenticate.assert_called_once_with(auth_request)

    def test_authenticate_tries_authenticators_in_order(
        self,
        auth_request: AuthRequest,
        mock_unsupporting_authenticator: Mock,
        mock_authenticator: Mock,
        sample_principal: Principal,
    ):
        """Should try authenticators in order until one matches."""
        middleware = AuthenticationMiddleware(authenticators=[mock_unsupporting_authenticator, mock_authenticator])

        result = middleware.authenticate(auth_request)

        assert result.principal == sample_principal
        mock_unsupporting_authenticator.supports.assert_called_once()
        mock_unsupporting_authenticator.authenticate.assert_not_called()
        mock_authenticator.supports.assert_called_once()

    def test_authenticate_publishes_success_event(
        self,
        auth_request: AuthRequest,
        mock_authenticator: Mock,
        mock_event_publisher: Mock,
        sample_principal: Principal,
    ):
        """Should publish AuthenticationSucceeded event on success."""
        middleware = AuthenticationMiddleware(
            authenticators=[mock_authenticator],
            event_publisher=mock_event_publisher,
        )

        middleware.authenticate(auth_request)

        mock_event_publisher.assert_called_once()
        event = mock_event_publisher.call_args[0][0]
        assert isinstance(event, AuthenticationSucceeded)
        assert event.principal_id == sample_principal.id.value
        assert event.auth_method == "MockAuthenticator"
        assert event.source_ip == auth_request.source_ip

    def test_authenticate_clears_rate_limit_on_success(
        self,
        auth_request: AuthRequest,
        mock_authenticator: Mock,
        mock_rate_limiter: Mock,
    ):
        """Should clear rate limit on successful authentication."""
        middleware = AuthenticationMiddleware(
            authenticators=[mock_authenticator],
            rate_limiter=mock_rate_limiter,
        )

        middleware.authenticate(auth_request)

        mock_rate_limiter.record_success.assert_called_once_with(auth_request.source_ip)


class TestAuthenticationMiddlewareFailure:
    """Tests for authentication failure scenarios."""

    def test_authenticate_raises_on_failure(
        self,
        auth_request: AuthRequest,
        mock_failing_authenticator: Mock,
    ):
        """Should raise AuthenticationError when authenticator fails."""
        middleware = AuthenticationMiddleware(authenticators=[mock_failing_authenticator])

        with pytest.raises(AuthenticationError) as exc_info:
            middleware.authenticate(auth_request)

        assert "Invalid credentials" in str(exc_info.value)

    def test_authenticate_publishes_failure_event(
        self,
        auth_request: AuthRequest,
        mock_failing_authenticator: Mock,
        mock_event_publisher: Mock,
    ):
        """Should publish AuthenticationFailed event on failure."""
        middleware = AuthenticationMiddleware(
            authenticators=[mock_failing_authenticator],
            event_publisher=mock_event_publisher,
        )

        with pytest.raises(AuthenticationError):
            middleware.authenticate(auth_request)

        mock_event_publisher.assert_called_once()
        event = mock_event_publisher.call_args[0][0]
        assert isinstance(event, AuthenticationFailed)
        assert event.source_ip == auth_request.source_ip

    def test_authenticate_records_rate_limit_failure(
        self,
        auth_request: AuthRequest,
        mock_failing_authenticator: Mock,
        mock_rate_limiter: Mock,
    ):
        """Should record rate limit failure on authentication failure."""
        middleware = AuthenticationMiddleware(
            authenticators=[mock_failing_authenticator],
            rate_limiter=mock_rate_limiter,
        )

        with pytest.raises(AuthenticationError):
            middleware.authenticate(auth_request)

        mock_rate_limiter.record_failure.assert_called_once_with(auth_request.source_ip)


class TestAuthenticationMiddlewareAnonymous:
    """Tests for anonymous authentication scenarios."""

    def test_anonymous_allowed_when_no_authenticator_matches(
        self,
        auth_request: AuthRequest,
        mock_unsupporting_authenticator: Mock,
    ):
        """Should return anonymous context when no authenticator matches and allowed."""
        middleware = AuthenticationMiddleware(
            authenticators=[mock_unsupporting_authenticator],
            allow_anonymous=True,
        )

        result = middleware.authenticate(auth_request)

        assert result.auth_method == "anonymous"
        assert result.principal.is_anonymous()

    def test_anonymous_denied_raises_missing_credentials(
        self,
        auth_request: AuthRequest,
        mock_unsupporting_authenticator: Mock,
    ):
        """Should raise MissingCredentialsError when anonymous not allowed."""
        middleware = AuthenticationMiddleware(
            authenticators=[mock_unsupporting_authenticator],
            allow_anonymous=False,
        )

        with pytest.raises(MissingCredentialsError) as exc_info:
            middleware.authenticate(auth_request)

        assert "No valid credentials" in str(exc_info.value)

    def test_anonymous_with_empty_authenticators(self, auth_request: AuthRequest):
        """Should allow anonymous with empty authenticator list."""
        middleware = AuthenticationMiddleware(authenticators=[], allow_anonymous=True)

        result = middleware.authenticate(auth_request)

        assert result.principal.is_anonymous()


class TestAuthenticationMiddlewareRateLimiting:
    """Tests for rate limiting functionality."""

    def test_rate_limit_blocks_request(
        self,
        auth_request: AuthRequest,
        mock_authenticator: Mock,
        mock_blocking_rate_limiter: Mock,
    ):
        """Should raise RateLimitExceededError when rate limited."""
        middleware = AuthenticationMiddleware(
            authenticators=[mock_authenticator],
            rate_limiter=mock_blocking_rate_limiter,
        )

        with pytest.raises(RateLimitExceededError) as exc_info:
            middleware.authenticate(auth_request)

        assert "Retry in 60s" in str(exc_info.value)
        mock_authenticator.authenticate.assert_not_called()

    def test_rate_limit_checked_before_authentication(
        self,
        auth_request: AuthRequest,
        mock_authenticator: Mock,
        mock_rate_limiter: Mock,
    ):
        """Rate limit should be checked before attempting authentication."""
        middleware = AuthenticationMiddleware(
            authenticators=[mock_authenticator],
            rate_limiter=mock_rate_limiter,
        )

        middleware.authenticate(auth_request)

        # Rate limit check should happen before authenticate
        assert mock_rate_limiter.check_rate_limit.call_count == 1
        mock_rate_limiter.check_rate_limit.assert_called_with(auth_request.source_ip)


class TestAuthenticationMiddlewareEventPublishing:
    """Tests for event publishing error handling."""

    def test_event_publisher_error_does_not_fail_authentication(
        self,
        auth_request: AuthRequest,
        mock_authenticator: Mock,
        sample_principal: Principal,
    ):
        """Authentication should succeed even if event publishing fails."""
        failing_publisher = Mock(side_effect=Exception("Publisher error"))
        middleware = AuthenticationMiddleware(
            authenticators=[mock_authenticator],
            event_publisher=failing_publisher,
        )

        # Should not raise despite publisher failure
        result = middleware.authenticate(auth_request)

        assert result.principal == sample_principal


# --- AuthorizationMiddleware Tests ---


@pytest.fixture
def mock_allowing_authorizer() -> Mock:
    """Create a mock authorizer that allows access."""
    authorizer = Mock(spec=IAuthorizer)
    authorizer.authorize.return_value = AuthorizationResult(
        allowed=True,
        matched_role="admin",
    )
    return authorizer


@pytest.fixture
def mock_denying_authorizer() -> Mock:
    """Create a mock authorizer that denies access."""
    authorizer = Mock(spec=IAuthorizer)
    authorizer.authorize.return_value = AuthorizationResult(
        allowed=False,
        reason="Insufficient permissions",
    )
    return authorizer


class TestAuthorizationMiddlewareSuccess:
    """Tests for successful authorization scenarios."""

    def test_authorize_success(
        self,
        mock_allowing_authorizer: Mock,
        sample_principal: Principal,
    ):
        """Should not raise when authorization succeeds."""
        middleware = AuthorizationMiddleware(authorizer=mock_allowing_authorizer)

        # Should not raise
        middleware.authorize(
            principal=sample_principal,
            action="invoke",
            resource_type="tool",
            resource_id="math:add",
        )

        mock_allowing_authorizer.authorize.assert_called_once()

    def test_authorize_publishes_granted_event(
        self,
        mock_allowing_authorizer: Mock,
        mock_event_publisher: Mock,
        sample_principal: Principal,
    ):
        """Should publish AuthorizationGranted event on success."""
        middleware = AuthorizationMiddleware(
            authorizer=mock_allowing_authorizer,
            event_publisher=mock_event_publisher,
        )

        middleware.authorize(
            principal=sample_principal,
            action="invoke",
            resource_type="tool",
            resource_id="math:add",
        )

        mock_event_publisher.assert_called_once()
        event = mock_event_publisher.call_args[0][0]
        assert isinstance(event, AuthorizationGranted)
        assert event.principal_id == sample_principal.id.value
        assert event.action == "invoke"
        assert event.resource_type == "tool"
        assert event.resource_id == "math:add"

    def test_authorize_passes_context_to_authorizer(
        self,
        mock_allowing_authorizer: Mock,
        sample_principal: Principal,
    ):
        """Should pass context dict to authorizer."""
        middleware = AuthorizationMiddleware(authorizer=mock_allowing_authorizer)
        context = {"tenant_id": "tenant-123", "custom_field": "value"}

        middleware.authorize(
            principal=sample_principal,
            action="read",
            resource_type="provider",
            resource_id="math",
            context=context,
        )

        call_args = mock_allowing_authorizer.authorize.call_args[0][0]
        assert call_args.context == context


class TestAuthorizationMiddlewareFailure:
    """Tests for authorization failure scenarios."""

    def test_authorize_raises_access_denied(
        self,
        mock_denying_authorizer: Mock,
        sample_principal: Principal,
    ):
        """Should raise AccessDeniedError when authorization fails."""
        middleware = AuthorizationMiddleware(authorizer=mock_denying_authorizer)

        with pytest.raises(AccessDeniedError) as exc_info:
            middleware.authorize(
                principal=sample_principal,
                action="delete",
                resource_type="provider",
                resource_id="important",
            )

        assert exc_info.value.principal_id == sample_principal.id.value
        assert exc_info.value.action == "delete"
        assert "provider:important" in exc_info.value.resource

    def test_authorize_publishes_denied_event(
        self,
        mock_denying_authorizer: Mock,
        mock_event_publisher: Mock,
        sample_principal: Principal,
    ):
        """Should publish AuthorizationDenied event on failure."""
        middleware = AuthorizationMiddleware(
            authorizer=mock_denying_authorizer,
            event_publisher=mock_event_publisher,
        )

        with pytest.raises(AccessDeniedError):
            middleware.authorize(
                principal=sample_principal,
                action="delete",
                resource_type="provider",
                resource_id="important",
            )

        mock_event_publisher.assert_called_once()
        event = mock_event_publisher.call_args[0][0]
        assert isinstance(event, AuthorizationDenied)
        assert event.reason == "Insufficient permissions"


class TestAuthorizationMiddlewareCheck:
    """Tests for check method (non-throwing authorization)."""

    def test_check_returns_true_when_allowed(
        self,
        mock_allowing_authorizer: Mock,
        sample_principal: Principal,
    ):
        """check() should return True when authorized."""
        middleware = AuthorizationMiddleware(authorizer=mock_allowing_authorizer)

        result = middleware.check(
            principal=sample_principal,
            action="read",
            resource_type="tool",
            resource_id="any",
        )

        assert result is True

    def test_check_returns_false_when_denied(
        self,
        mock_denying_authorizer: Mock,
        sample_principal: Principal,
    ):
        """check() should return False when not authorized (no exception)."""
        middleware = AuthorizationMiddleware(authorizer=mock_denying_authorizer)

        result = middleware.check(
            principal=sample_principal,
            action="delete",
            resource_type="provider",
            resource_id="any",
        )

        assert result is False

    def test_check_does_not_publish_events(
        self,
        mock_allowing_authorizer: Mock,
        mock_event_publisher: Mock,
        sample_principal: Principal,
    ):
        """check() should not publish events."""
        middleware = AuthorizationMiddleware(
            authorizer=mock_allowing_authorizer,
            event_publisher=mock_event_publisher,
        )

        middleware.check(
            principal=sample_principal,
            action="read",
            resource_type="tool",
            resource_id="any",
        )

        # check() doesn't publish events - only authorize() does
        mock_event_publisher.assert_not_called()


# --- Helper Function Tests ---


class TestCreateAuthRequestFromHeaders:
    """Tests for create_auth_request_from_headers helper."""

    def test_creates_request_with_all_parameters(self):
        """Should create AuthRequest with all provided parameters."""
        headers = {"Authorization": "Bearer token123", "X-Custom": "value"}

        request = create_auth_request_from_headers(
            headers=headers,
            source_ip="10.0.0.1",
            method="POST",
            path="/api/invoke",
        )

        assert request.source_ip == "10.0.0.1"
        assert request.method == "POST"
        assert request.path == "/api/invoke"

    def test_normalizes_headers_to_lowercase(self):
        """Should normalize header keys to lowercase."""
        headers = {"Authorization": "Bearer token", "X-API-KEY": "secret"}

        request = create_auth_request_from_headers(headers=headers)

        assert "authorization" in request.headers
        assert "x-api-key" in request.headers
        assert request.headers["authorization"] == "Bearer token"

    def test_preserves_original_case_headers(self):
        """Should also preserve original case for backwards compatibility."""
        headers = {"Authorization": "Bearer token"}

        request = create_auth_request_from_headers(headers=headers)

        # Both cases should be present
        assert "Authorization" in request.headers
        assert "authorization" in request.headers

    def test_uses_defaults_for_optional_parameters(self):
        """Should use sensible defaults for optional parameters."""
        request = create_auth_request_from_headers(headers={})

        assert request.source_ip == "unknown"
        assert request.method == ""
        assert request.path == ""


# --- Integration-like Tests ---


class TestMiddlewareChain:
    """Tests for using authentication and authorization together."""

    def test_full_auth_flow(
        self,
        auth_request: AuthRequest,
        mock_authenticator: Mock,
        mock_allowing_authorizer: Mock,
        sample_principal: Principal,
    ):
        """Test complete authentication -> authorization flow."""
        auth_middleware = AuthenticationMiddleware(authenticators=[mock_authenticator])
        authz_middleware = AuthorizationMiddleware(authorizer=mock_allowing_authorizer)

        # Step 1: Authenticate
        auth_context = auth_middleware.authenticate(auth_request)
        assert auth_context.is_authenticated()

        # Step 2: Authorize
        authz_middleware.authorize(
            principal=auth_context.principal,
            action="invoke",
            resource_type="tool",
            resource_id="math:add",
        )

        # Both middlewares should have been called
        mock_authenticator.authenticate.assert_called_once()
        mock_allowing_authorizer.authorize.assert_called_once()

    def test_auth_flow_with_denial(
        self,
        auth_request: AuthRequest,
        mock_authenticator: Mock,
        mock_denying_authorizer: Mock,
    ):
        """Test authentication succeeds but authorization fails."""
        auth_middleware = AuthenticationMiddleware(authenticators=[mock_authenticator])
        authz_middleware = AuthorizationMiddleware(authorizer=mock_denying_authorizer)

        # Step 1: Authenticate (succeeds)
        auth_context = auth_middleware.authenticate(auth_request)

        # Step 2: Authorize (fails)
        with pytest.raises(AccessDeniedError):
            authz_middleware.authorize(
                principal=auth_context.principal,
                action="admin",
                resource_type="system",
                resource_id="config",
            )
