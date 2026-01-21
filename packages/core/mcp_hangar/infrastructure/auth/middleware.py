"""Authentication and Authorization middleware.

Provides middleware components that can be integrated with HTTP frameworks
(Starlette, FastAPI) or used directly in application code.

Design principles:
- Single Responsibility: Each middleware handles one concern
- Open/Closed: Easy to extend with new authenticators
- Dependency Inversion: Depends on abstractions (IAuthenticator, IAuthorizer)
"""

from dataclasses import dataclass
from typing import Protocol

import structlog

from ...domain.contracts.authentication import AuthRequest, IAuthenticator
from ...domain.contracts.authorization import AuthorizationRequest, IAuthorizer
from ...domain.events import AuthenticationFailed, AuthenticationSucceeded, AuthorizationDenied, AuthorizationGranted
from ...domain.exceptions import AccessDeniedError, AuthenticationError, MissingCredentialsError, RateLimitExceededError
from ...domain.value_objects import Principal
from .rate_limiter import AuthRateLimiter

logger = structlog.get_logger(__name__)


class EventPublisher(Protocol):
    """Protocol for event publishing."""

    def __call__(self, event: object) -> None:
        """Publish a domain event."""
        ...


@dataclass(frozen=True)
class AuthContext:
    """Authentication context attached to requests.

    Immutable container for authentication result.

    Attributes:
        principal: The authenticated principal.
        auth_method: Name of the authenticator that handled the request.
    """

    principal: Principal
    auth_method: str

    def is_authenticated(self) -> bool:
        """Check if request is authenticated (not anonymous)."""
        return not self.principal.is_anonymous()


class AuthenticationMiddleware:
    """Middleware that authenticates incoming requests.

    Implements Chain of Responsibility pattern - tries each registered
    authenticator in order until one succeeds.

    If no authenticator handles the request, returns anonymous principal
    (if allowed) or raises MissingCredentialsError.
    """

    def __init__(
        self,
        authenticators: list[IAuthenticator],
        allow_anonymous: bool = False,
        event_publisher: EventPublisher | None = None,
        rate_limiter: AuthRateLimiter | None = None,
    ):
        """Initialize the authentication middleware.

        Args:
            authenticators: List of authenticators to try in order.
            allow_anonymous: If True, return anonymous principal when no auth provided.
            event_publisher: Optional callback to publish domain events.
            rate_limiter: Optional rate limiter for brute-force protection.
        """
        self._authenticators = list(authenticators)  # Defensive copy
        self._allow_anonymous = allow_anonymous
        self._event_publisher = event_publisher
        self._rate_limiter = rate_limiter

    def authenticate(self, request: AuthRequest) -> AuthContext:
        """Authenticate request and return context.

        Args:
            request: The normalized authentication request.

        Returns:
            AuthContext with authenticated principal.

        Raises:
            AuthenticationError: If authentication fails.
            MissingCredentialsError: If no credentials and anonymous not allowed.
            RateLimitExceededError: If rate limit exceeded for this IP.
        """
        self._check_rate_limit(request)

        for authenticator in self._authenticators:
            if authenticator.supports(request):
                return self._try_authenticate(authenticator, request)

        return self._handle_no_authenticator_matched(request)

    def _check_rate_limit(self, request: AuthRequest) -> None:
        """Check rate limit before authentication attempt.

        Raises:
            RateLimitExceededError: If rate limit exceeded.
        """
        if not self._rate_limiter:
            return

        rate_result = self._rate_limiter.check_rate_limit(request.source_ip)
        if not rate_result.allowed:
            logger.warning(
                "auth_rate_limit_blocked",
                source_ip=request.source_ip,
                reason=rate_result.reason,
                retry_after=rate_result.retry_after,
            )
            raise RateLimitExceededError(
                message=f"Too many auth attempts. Retry in {int(rate_result.retry_after or 0)}s.",
                retry_after=rate_result.retry_after,
            )

    def _try_authenticate(self, authenticator: IAuthenticator, request: AuthRequest) -> AuthContext:
        """Try to authenticate with a specific authenticator.

        Args:
            authenticator: The authenticator to use.
            request: The authentication request.

        Returns:
            AuthContext on success.

        Raises:
            AuthenticationError: If authentication fails.
        """
        try:
            principal = authenticator.authenticate(request)
            return self._handle_authentication_success(authenticator, principal, request)
        except AuthenticationError as e:
            self._handle_authentication_failure(authenticator, request, e)
            raise

    def _handle_authentication_success(
        self,
        authenticator: IAuthenticator,
        principal: Principal,
        request: AuthRequest,
    ) -> AuthContext:
        """Handle successful authentication."""
        auth_method = authenticator.__class__.__name__

        self._publish_event(
            AuthenticationSucceeded(
                principal_id=principal.id.value,
                principal_type=principal.type.value,
                auth_method=auth_method,
                source_ip=request.source_ip,
                tenant_id=principal.tenant_id,
            )
        )

        logger.info(
            "authentication_succeeded",
            principal_id=principal.id.value,
            auth_method=auth_method,
            source_ip=request.source_ip,
        )

        if self._rate_limiter:
            self._rate_limiter.record_success(request.source_ip)

        return AuthContext(principal=principal, auth_method=auth_method)

    def _handle_authentication_failure(
        self,
        authenticator: IAuthenticator,
        request: AuthRequest,
        error: AuthenticationError,
    ) -> None:
        """Handle failed authentication attempt."""
        if self._rate_limiter:
            self._rate_limiter.record_failure(request.source_ip)

        self._publish_event(
            AuthenticationFailed(
                auth_method=authenticator.__class__.__name__,
                source_ip=request.source_ip,
                reason=error.message,
            )
        )

    def _handle_no_authenticator_matched(self, request: AuthRequest) -> AuthContext:
        """Handle case when no authenticator matched the request.

        Returns:
            Anonymous AuthContext if allowed.

        Raises:
            MissingCredentialsError: If anonymous not allowed.
        """
        if self._allow_anonymous:
            logger.debug(
                "anonymous_access",
                source_ip=request.source_ip,
                path=request.path,
            )
            return AuthContext(principal=Principal.anonymous(), auth_method="anonymous")

        expected_methods = [a.__class__.__name__ for a in self._authenticators]
        raise MissingCredentialsError(
            message="No valid credentials provided",
            expected_methods=expected_methods,
        )

    def _publish_event(self, event) -> None:
        """Publish domain event if publisher is configured."""
        if self._event_publisher:
            try:
                self._event_publisher(event)
            except Exception as e:
                logger.warning("event_publish_failed", event_type=type(event).__name__, error=str(e))


class AuthorizationMiddleware:
    """Middleware that checks authorization for requests.

    Integrates with an authorizer to check permissions and emits
    domain events for audit trail.
    """

    def __init__(
        self,
        authorizer: IAuthorizer,
        event_publisher: EventPublisher | None = None,
    ):
        """Initialize the authorization middleware.

        Args:
            authorizer: The authorizer to use for access decisions.
            event_publisher: Optional callback to publish domain events.
        """
        self._authorizer = authorizer
        self._event_publisher = event_publisher

    def authorize(
        self,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: str,
        context: dict | None = None,
    ) -> None:
        """Check authorization, raise AccessDeniedError if denied.

        Args:
            principal: The authenticated principal.
            action: The action being performed.
            resource_type: Type of resource being accessed.
            resource_id: Specific resource identifier.
            context: Optional additional context for policy evaluation.

        Raises:
            AccessDeniedError: If the principal is not authorized.
        """
        request = self._create_authorization_request(principal, action, resource_type, resource_id, context)
        result = self._authorizer.authorize(request)

        if result.allowed:
            self._handle_authorization_granted(principal, action, resource_type, resource_id, result)
            return

        self._handle_authorization_denied(principal, action, resource_type, resource_id, result)

    def check(
        self,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: str,
        context: dict | None = None,
    ) -> bool:
        """Check authorization without raising exception.

        Use this for conditional logic where denial is not an error.

        Args:
            principal: The authenticated principal.
            action: The action being performed.
            resource_type: Type of resource being accessed.
            resource_id: Specific resource identifier.
            context: Optional additional context.

        Returns:
            True if authorized, False otherwise.
        """
        request = self._create_authorization_request(principal, action, resource_type, resource_id, context)
        result = self._authorizer.authorize(request)
        return result.allowed

    def _create_authorization_request(
        self,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: str,
        context: dict | None,
    ) -> AuthorizationRequest:
        """Create authorization request from parameters."""
        return AuthorizationRequest(
            principal=principal,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            context=context or {},
        )

    def _handle_authorization_granted(
        self,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: str,
        result,
    ) -> None:
        """Handle successful authorization."""
        self._publish_event(
            AuthorizationGranted(
                principal_id=principal.id.value,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                granted_by_role=result.matched_role or "unknown",
            )
        )

    def _handle_authorization_denied(
        self,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: str,
        result,
    ) -> None:
        """Handle denied authorization.

        Raises:
            AccessDeniedError: Always raised with denial details.
        """
        self._publish_event(
            AuthorizationDenied(
                principal_id=principal.id.value,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                reason=result.reason,
            )
        )

        raise AccessDeniedError(
            principal_id=principal.id.value,
            action=action,
            resource=f"{resource_type}:{resource_id}",
            reason=result.reason,
        )

    def _publish_event(self, event: object) -> None:
        """Publish domain event if publisher is configured."""
        if self._event_publisher:
            try:
                self._event_publisher(event)
            except Exception as e:
                logger.warning(
                    "event_publish_failed",
                    event_type=type(event).__name__,
                    error=str(e),
                )


def create_auth_request_from_headers(
    headers: dict[str, str],
    source_ip: str = "unknown",
    method: str = "",
    path: str = "",
) -> AuthRequest:
    """Create AuthRequest from HTTP headers.

    Convenience function for creating AuthRequest from HTTP request data.

    Args:
        headers: HTTP headers (case-insensitive dict preferred).
        source_ip: Client IP address.
        method: HTTP method.
        path: Request path.

    Returns:
        AuthRequest ready for authentication.
    """
    # Normalize headers to lowercase for consistent lookup
    normalized_headers = {k.lower(): v for k, v in headers.items()}
    # Also keep original case for backwards compatibility
    normalized_headers.update(headers)

    return AuthRequest(
        headers=normalized_headers,
        source_ip=source_ip,
        method=method,
        path=path,
    )
