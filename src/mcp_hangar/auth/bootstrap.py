"""Authentication and Authorization bootstrap.

Initializes auth components based on configuration and wires them together.
This is the composition root for auth infrastructure.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from mcp_hangar.domain.contracts.authentication import IApiKeyStore, IAuthenticator
from mcp_hangar.domain.contracts.authorization import IAuthorizer, IRoleStore
from mcp_hangar.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator, InMemoryApiKeyStore
from mcp_hangar.auth.infrastructure.jwt_authenticator import (
    JWKSTokenValidator,
    JWTAuthenticator,
    MultiIssuerTokenValidator,
    OIDCConfig,
)
from mcp_hangar.auth.infrastructure.middleware import AuthenticationMiddleware, AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.opa_authorizer import CombinedAuthorizer, OPAAuthorizer
from mcp_hangar.auth.infrastructure.rate_limiter import AuthRateLimitConfig, AuthRateLimiter
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.auth.config import AuthConfig, OIDCIssuerConfig

logger = structlog.get_logger(__name__)


def _create_storage_backends(
    config: AuthConfig,
    event_publisher: Callable | None = None,
    event_store=None,
    event_bus=None,
) -> tuple[IApiKeyStore, IRoleStore, Any]:
    """Create storage backends based on configuration.

    Args:
        config: Auth configuration with storage settings.
        event_publisher: Optional callback for publishing domain events.
            For CQRS integration, pass EventBus.publish.
        event_store: Optional event store for event_sourcing driver.
        event_bus: Optional event bus for event_sourcing driver.

    Returns:
        Tuple of (api_key_store, role_store, tap_store).

    Raises:
        ValueError: If unknown storage driver is specified.
    """
    driver = config.storage.driver.lower()

    if driver == "memory":
        logger.info("auth_storage_memory", warning="Data will be lost on restart")
        api_key_store: IApiKeyStore = InMemoryApiKeyStore()
        role_store: IRoleStore = InMemoryRoleStore()
        tap_store: Any = None

    elif driver == "event_sourcing":
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore, EventSourcedRoleStore

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
        tap_store = None

    elif driver == "sqlite":
        from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore, SQLiteRoleStore

        # Ensure directory exists
        db_path = Path(config.storage.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("auth_storage_sqlite", path=str(db_path))

        sqlite_api_key_store = SQLiteApiKeyStore(db_path, event_publisher=event_publisher)
        sqlite_api_key_store.initialize()
        api_key_store = sqlite_api_key_store

        sqlite_role_store = SQLiteRoleStore(db_path, event_publisher=event_publisher)
        sqlite_role_store.initialize()
        role_store = sqlite_role_store

        from mcp_hangar.auth.infrastructure.sqlite_tap_store import SQLiteToolAccessPolicyStore

        tap_store = SQLiteToolAccessPolicyStore(db_path)

    elif driver == "postgresql" or driver == "postgres":
        from mcp_hangar.auth.infrastructure.postgres_store import (
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

        pg_api_key_store = PostgresApiKeyStore(connection_factory, event_publisher=event_publisher)
        pg_api_key_store.initialize()
        api_key_store = pg_api_key_store

        pg_role_store = PostgresRoleStore(connection_factory, event_publisher=event_publisher)
        pg_role_store.initialize()
        role_store = pg_role_store
        tap_store = None

    else:
        raise ValueError(
            f"Unknown auth storage driver: {driver}. Use 'memory', 'event_sourcing', 'sqlite', or 'postgresql'."
        )

    return api_key_store, role_store, tap_store


class AuthComponents:
    """Container for initialized auth components.

    Provides access to all auth infrastructure for use by the application.

    Attributes:
        authn_middleware: Authentication middleware.
        authz_middleware: AuthorizationMiddleware.
        api_key_store: API key storage (for key management).
        role_store: Role storage (for role management).
        tap_store: Tool access policy storage (for TAP management).
        oidc_issuer: OIDC issuer URL when Bearer/OIDC auth is configured; empty string otherwise.
            Used to populate the PRM endpoint and WWW-Authenticate header (RFC 9728).
            For multi-issuer configs this holds the first trusted issuer for backward
            compatibility; a later slice migrates consumers to ``oidc_issuers``.
        oidc_issuers: All trusted OIDC issuer URLs when Bearer/OIDC auth is configured.
            Empty list when OIDC is disabled or unconfigured.
        oidc_resource_uri: Configured public resource URI (from auth.oidc.resource_uri).
            When set, overrides Host-derived URL in PRM / WWW-Authenticate responses.
    """

    def __init__(
        self,
        authn_middleware: AuthenticationMiddleware,
        authz_middleware: AuthorizationMiddleware,
        api_key_store: IApiKeyStore | None = None,
        role_store: IRoleStore | None = None,
        tap_store: Any | None = None,
        oidc_issuer: str = "",
        oidc_issuers: list[str] | None = None,
        oidc_resource_uri: str = "",
    ):
        self.authn_middleware = authn_middleware
        self.authz_middleware = authz_middleware
        self.api_key_store = api_key_store
        self.role_store = role_store
        self.tap_store = tap_store
        self.oidc_issuer = oidc_issuer
        self.oidc_issuers = oidc_issuers if oidc_issuers is not None else []
        self.oidc_resource_uri = oidc_resource_uri

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
        from mcp_hangar.domain.value_objects import Principal

        class NullAuthenticator:
            def supports(self, request):
                return True

            def authenticate(self, request):
                return Principal.system()

        class NullAuthorizer:
            def authorize(self, request):
                from mcp_hangar.domain.contracts.authorization import AuthorizationResult

                return AuthorizationResult.allow(reason="auth_disabled")

        super().__init__(
            authn_middleware=AuthenticationMiddleware([NullAuthenticator()], allow_anonymous=True),
            authz_middleware=AuthorizationMiddleware(NullAuthorizer()),
        )

    @property
    def enabled(self) -> bool:
        return False


def _replay_tap_policies(tap_store: Any) -> None:
    """Replay persisted TAP policies into the in-memory ToolAccessResolver on startup.

    Reads all rows from the SQLiteToolAccessPolicyStore and applies them to the
    ToolAccessResolver singleton so runtime enforcement is consistent with the
    persisted state after a server restart.

    Args:
        tap_store: SQLiteToolAccessPolicyStore instance.
    """
    from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver
    from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

    try:
        from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver

        resolver = get_tool_access_resolver()
    except ImportError:
        from mcp_hangar.domain.services import tool_access_resolver as _tap_module

        resolver = getattr(_tap_module, "_resolver", None) or ToolAccessResolver()

    if resolver is None:
        logger.warning("tap_replay_skipped", reason="resolver_not_initialized")
        return

    policies = tap_store.list_all_policies()
    for scope, target_id, allow_list, deny_list in policies:
        policy = ToolAccessPolicy(
            allow_list=tuple(allow_list),
            deny_list=tuple(deny_list),
        )
        try:
            if scope == "provider":
                resolver.set_mcp_server_policy(target_id, policy)
            elif scope == "group":
                resolver.set_group_policy(target_id, policy)
            elif scope == "member":
                parts = target_id.split(":", 1)
                if len(parts) == 2:
                    resolver.set_member_policy(parts[0], parts[1], policy)
                else:
                    resolver.set_member_policy(target_id, target_id, policy)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: replay failure must not prevent startup
            logger.warning(
                "tap_policy_replay_failed",
                scope=scope,
                target_id=target_id,
                error=str(e),
            )

    logger.info("tap_policies_replayed", count=len(policies))


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
    api_key_store, role_store, tap_store = _create_storage_backends(
        config,
        event_publisher=event_publisher,
        event_store=event_store,
        event_bus=event_bus,
    )

    authenticators: list[IAuthenticator] = []

    # Initialize API Key authentication
    if config.api_key.enabled:
        authenticators.append(
            ApiKeyAuthenticator(
                key_store=api_key_store,
                header_name=config.api_key.header_name,
            )
        )
        logger.info("api_key_auth_enabled", header_name=config.api_key.header_name)

    # Initialize OIDC/JWT authentication (single or multi-issuer trust registry)
    issuer_cfgs: list[OIDCIssuerConfig] = []
    if config.oidc.enabled:
        issuer_cfgs = config.oidc.resolved_issuers()
        if not issuer_cfgs:
            logger.warning("oidc_config_incomplete", issuer=config.oidc.issuer, audience=config.oidc.audience)
        else:
            # RFC 8707 resource binding: when a resource URI is configured, every
            # accepted token's `aud` must match it (the same value advertised as
            # PRM `resource`), so a token minted for another resource is rejected.
            # This intentionally overrides any per-issuer `audience`. Without a
            # configured resource URI we fall back to the per-issuer audience
            # (the Host-derived PRM value is advertisement-only and never trusted
            # as a validation audience).
            resource_audience = config.oidc.resource_uri
            oidc_configs = [
                OIDCConfig(
                    issuer=entry.issuer,
                    audience=resource_audience or entry.audience,
                    jwks_uri=entry.jwks_uri,
                    client_id=entry.client_id,
                    subject_claim=entry.subject_claim,
                    groups_claim=entry.groups_claim,
                    tenant_claim=entry.tenant_claim,
                    email_claim=entry.email_claim,
                    max_token_lifetime=entry.max_token_lifetime_seconds,
                    require_tenant=entry.require_tenant,
                )
                for entry in issuer_cfgs
            ]
            # Warn on duplicate issuer strings: the validator registry and the
            # per-issuer config map are keyed by issuer, so a duplicate silently
            # drops the earlier entry (last-wins).
            _issuers = [c.issuer for c in oidc_configs]
            _dupes = sorted({i for i in _issuers if _issuers.count(i) > 1})
            if _dupes:
                logger.warning("oidc_duplicate_issuers", issuers=_dupes)
            validators = [JWKSTokenValidator(oidc_config) for oidc_config in oidc_configs]
            multi_validator = MultiIssuerTokenValidator(validators)
            # Per-issuer config map so the authenticator applies each issuer's own
            # claim mappings and lifetime limit (selected by the validated `iss`);
            # the first config is the fallback default for single-issuer setups.
            issuer_config_map = {oidc_config.issuer: oidc_config for oidc_config in oidc_configs}
            authenticators.append(JWTAuthenticator(oidc_configs[0], multi_validator, issuer_configs=issuer_config_map))
            logger.info(
                "oidc_auth_enabled",
                issuer_count=len(issuer_cfgs),
                issuers=[c.issuer for c in issuer_cfgs],
                resource=resource_audience or None,
                audience_bound_to_resource=bool(resource_audience),
                require_tenant=any(c.require_tenant for c in oidc_configs),
            )

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

    # Replay TAP policies from SQLite into the in-memory resolver on startup
    if tap_store is not None:
        _replay_tap_policies(tap_store)

    return AuthComponents(
        authn_middleware=authn_middleware,
        authz_middleware=authz_middleware,
        api_key_store=api_key_store,
        role_store=role_store,
        tap_store=tap_store,
        oidc_issuer=issuer_cfgs[0].issuer if issuer_cfgs else "",
        oidc_issuers=[c.issuer for c in issuer_cfgs],
        oidc_resource_uri=config.oidc.resource_uri,
    )
