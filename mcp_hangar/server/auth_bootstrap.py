"""Authentication and Authorization bootstrap.

Initializes auth components based on configuration and wires them together.
This is the composition root for auth infrastructure.
"""

from pathlib import Path
from typing import Callable

import structlog

from ..domain.contracts.authentication import IApiKeyStore
from ..domain.contracts.authorization import IAuthorizer, IRoleStore
from ..infrastructure.auth.api_key_authenticator import ApiKeyAuthenticator, InMemoryApiKeyStore
from ..infrastructure.auth.jwt_authenticator import JWKSTokenValidator, JWTAuthenticator, OIDCConfig
from ..infrastructure.auth.middleware import AuthenticationMiddleware, AuthorizationMiddleware
from ..infrastructure.auth.opa_authorizer import CombinedAuthorizer, OPAAuthorizer
from ..infrastructure.auth.rate_limiter import AuthRateLimitConfig, AuthRateLimiter
from ..infrastructure.auth.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from .auth_config import AuthConfig

logger = structlog.get_logger(__name__)


def _create_storage_backends(
    config: AuthConfig,
    event_publisher: Callable | None = None,
    event_store=None,
    event_bus=None,
) -> tuple[IApiKeyStore, IRoleStore]:
    """Create storage backends based on configuration.

    Args:
        config: Auth configuration with storage settings.
        event_publisher: Optional callback for publishing domain events.
            For CQRS integration, pass EventBus.publish.
        event_store: Optional event store for event_sourcing driver.
        event_bus: Optional event bus for event_sourcing driver.

    Returns:
        Tuple of (api_key_store, role_store).

    Raises:
        ValueError: If unknown storage driver is specified.
    """
    driver = config.storage.driver.lower()

    if driver == "memory":
        logger.info("auth_storage_memory", warning="Data will be lost on restart")
        api_key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

    elif driver == "event_sourcing":
        from ..infrastructure.auth.event_sourced_store import EventSourcedApiKeyStore, EventSourcedRoleStore

        if event_store is None:
            raise ValueError("event_sourcing driver requires event_store to be provided")

        logger.info("auth_storage_event_sourcing")

        api_key_store = EventSourcedApiKeyStore(
            event_store=event_store,
            event_publisher=event_bus,
        )
        role_store = EventSourcedRoleStore(
            event_store=event_store,
            event_publisher=event_bus,
        )

    elif driver == "sqlite":
        from ..infrastructure.auth.sqlite_store import SQLiteApiKeyStore, SQLiteRoleStore

        # Ensure directory exists
        db_path = Path(config.storage.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("auth_storage_sqlite", path=str(db_path))

        api_key_store = SQLiteApiKeyStore(db_path, event_publisher=event_publisher)
        api_key_store.initialize()

        role_store = SQLiteRoleStore(db_path, event_publisher=event_publisher)
        role_store.initialize()

    elif driver == "postgresql" or driver == "postgres":
        from ..infrastructure.auth.postgres_store import (
            create_postgres_connection_factory,
            PostgresApiKeyStore,
            PostgresRoleStore,
        )

        logger.info(
            "auth_storage_postgresql",
            host=config.storage.host,
            port=config.storage.port,
            database=config.storage.database,
        )

        connection_factory = create_postgres_connection_factory(
            host=config.storage.host,
            port=config.storage.port,
            database=config.storage.database,
            user=config.storage.user,
            password=config.storage.password,
            min_connections=config.storage.min_connections,
            max_connections=config.storage.max_connections,
        )

        api_key_store = PostgresApiKeyStore(connection_factory, event_publisher=event_publisher)
        api_key_store.initialize()

        role_store = PostgresRoleStore(connection_factory, event_publisher=event_publisher)
        role_store.initialize()

    else:
        raise ValueError(
            f"Unknown auth storage driver: {driver}. " "Use 'memory', 'event_sourcing', 'sqlite', or 'postgresql'."
        )

    return api_key_store, role_store


class AuthComponents:
    """Container for initialized auth components.

    Provides access to all auth infrastructure for use by the application.

    Attributes:
        authn_middleware: Authentication middleware.
        authz_middleware: Authorization middleware.
        api_key_store: API key storage (for key management).
        role_store: Role storage (for role management).
    """

    def __init__(
        self,
        authn_middleware: AuthenticationMiddleware,
        authz_middleware: AuthorizationMiddleware,
        api_key_store: IApiKeyStore | None = None,
        role_store: IRoleStore | None = None,
    ):
        self.authn_middleware = authn_middleware
        self.authz_middleware = authz_middleware
        self.api_key_store = api_key_store
        self.role_store = role_store

    @property
    def enabled(self) -> bool:
        """Check if auth is enabled (has any authenticators)."""
        return len(self.authn_middleware._authenticators) > 0 or not self.authn_middleware._allow_anonymous


class NullAuthComponents(AuthComponents):
    """Null auth components for when auth is disabled.

    All authentication succeeds with system principal.
    All authorization is granted.
    """

    def __init__(self):
        from ..domain.value_objects import Principal

        class NullAuthenticator:
            def supports(self, request):
                return True

            def authenticate(self, request):
                return Principal.system()

        class NullAuthorizer:
            def authorize(self, request):
                from ..domain.contracts.authorization import AuthorizationResult

                return AuthorizationResult.allow(reason="auth_disabled")

        super().__init__(
            authn_middleware=AuthenticationMiddleware([NullAuthenticator()], allow_anonymous=True),
            authz_middleware=AuthorizationMiddleware(NullAuthorizer()),
        )

    @property
    def enabled(self) -> bool:
        return False


def bootstrap_auth(
    config: AuthConfig,
    event_publisher: Callable | None = None,
    event_store=None,
    event_bus=None,
) -> AuthComponents:
    """Bootstrap authentication and authorization components.

    Creates and configures all auth infrastructure based on configuration.

    Args:
        config: Auth configuration.
        event_publisher: Optional function to publish domain events.
        event_store: Optional event store for event_sourcing driver.
        event_bus: Optional event bus for event_sourcing driver.

    Returns:
        AuthComponents with initialized middleware and stores.
    """
    if not config.enabled:
        logger.info("auth_disabled", allow_anonymous=config.allow_anonymous)
        return NullAuthComponents()

    # Initialize storage backends based on configuration
    # Pass event_publisher for CQRS integration - stores will emit domain events
    api_key_store, role_store = _create_storage_backends(
        config,
        event_publisher=event_publisher,
        event_store=event_store,
        event_bus=event_bus,
    )

    authenticators = []

    # Initialize API Key authentication
    if config.api_key.enabled:
        authenticators.append(
            ApiKeyAuthenticator(
                key_store=api_key_store,
                header_name=config.api_key.header_name,
            )
        )
        logger.info("api_key_auth_enabled", header_name=config.api_key.header_name)

    # Initialize OIDC/JWT authentication
    if config.oidc.enabled:
        if not config.oidc.issuer or not config.oidc.audience:
            logger.warning("oidc_config_incomplete", issuer=config.oidc.issuer, audience=config.oidc.audience)
        else:
            oidc_config = OIDCConfig(
                issuer=config.oidc.issuer,
                audience=config.oidc.audience,
                jwks_uri=config.oidc.jwks_uri,
                client_id=config.oidc.client_id,
                subject_claim=config.oidc.subject_claim,
                groups_claim=config.oidc.groups_claim,
                tenant_claim=config.oidc.tenant_claim,
                email_claim=config.oidc.email_claim,
            )
            token_validator = JWKSTokenValidator(oidc_config)
            authenticators.append(JWTAuthenticator(oidc_config, token_validator))
            logger.info("oidc_auth_enabled", issuer=config.oidc.issuer)

    # Initialize rate limiter for brute-force protection
    rate_limiter = AuthRateLimiter(
        AuthRateLimitConfig(
            enabled=config.rate_limit.enabled,
            max_attempts=config.rate_limit.max_attempts,
            window_seconds=config.rate_limit.window_seconds,
            lockout_seconds=config.rate_limit.lockout_seconds,
        )
    )
    if config.rate_limit.enabled:
        logger.info(
            "auth_rate_limiter_enabled",
            max_attempts=config.rate_limit.max_attempts,
            window_seconds=config.rate_limit.window_seconds,
        )

    # Create authentication middleware
    authn_middleware = AuthenticationMiddleware(
        authenticators=authenticators,
        allow_anonymous=config.allow_anonymous,
        event_publisher=event_publisher,
        rate_limiter=rate_limiter if config.rate_limit.enabled else None,
    )

    # Apply static role assignments from config
    for assignment in config.role_assignments:
        if not assignment.principal or not assignment.role:
            logger.warning(
                "skipping_invalid_role_assignment",
                principal=assignment.principal,
                role=assignment.role,
            )
            continue

        try:
            role_store.assign_role(
                principal_id=assignment.principal,
                role_name=assignment.role,
                scope=assignment.scope,
            )
            logger.debug(
                "role_assigned_from_config",
                principal=assignment.principal,
                role=assignment.role,
                scope=assignment.scope,
            )
        except ValueError as e:
            logger.warning(
                "role_assignment_failed",
                principal=assignment.principal,
                role=assignment.role,
                error=str(e),
            )

    # Initialize authorizer
    rbac_authorizer = RBACAuthorizer(role_store)
    authorizer: IAuthorizer = rbac_authorizer

    # Optionally wrap with OPA
    if config.opa.enabled:
        opa_authorizer = OPAAuthorizer(
            opa_url=config.opa.url,
            policy_path=config.opa.policy_path,
            timeout=config.opa.timeout,
        )
        authorizer = CombinedAuthorizer(
            rbac_authorizer=rbac_authorizer,
            opa_authorizer=opa_authorizer,
            require_both=False,  # RBAC first, OPA as fallback
        )
        logger.info("opa_auth_enabled", url=config.opa.url)

    # Create authorization middleware
    authz_middleware = AuthorizationMiddleware(
        authorizer=authorizer,
        event_publisher=event_publisher,
    )

    logger.info(
        "auth_bootstrap_complete",
        authenticators_count=len(authenticators),
        allow_anonymous=config.allow_anonymous,
        role_assignments_count=len(config.role_assignments),
        opa_enabled=config.opa.enabled,
    )

    return AuthComponents(
        authn_middleware=authn_middleware,
        authz_middleware=authz_middleware,
        api_key_store=api_key_store,
        role_store=role_store,
    )
