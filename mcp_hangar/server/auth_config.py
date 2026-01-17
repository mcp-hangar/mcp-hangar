"""Authentication and Authorization configuration.

Defines dataclasses for auth configuration and functions to load
auth settings from YAML configuration files.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApiKeyAuthConfig:
    """API Key authentication configuration.

    Attributes:
        enabled: Whether API key authentication is enabled.
        header_name: Name of the HTTP header containing the API key.
    """

    enabled: bool = True
    header_name: str = "X-API-Key"


@dataclass
class OIDCAuthConfig:
    """OIDC/JWT authentication configuration.

    Attributes:
        enabled: Whether OIDC/JWT authentication is enabled.
        issuer: OIDC issuer URL (e.g., https://auth.company.com).
        audience: Expected audience claim value.
        jwks_uri: JWKS endpoint URL (auto-discovered from issuer if None).
        client_id: Optional client ID for additional validation.
        subject_claim: JWT claim for subject identifier.
        groups_claim: JWT claim for group memberships.
        tenant_claim: JWT claim for tenant identifier.
        email_claim: JWT claim for email address.
    """

    enabled: bool = False
    issuer: str = ""
    audience: str = ""
    jwks_uri: str | None = None
    client_id: str | None = None

    # Claim mappings
    subject_claim: str = "sub"
    groups_claim: str = "groups"
    tenant_claim: str = "tenant_id"
    email_claim: str = "email"


@dataclass
class OPAConfig:
    """OPA (Open Policy Agent) configuration.

    Attributes:
        enabled: Whether OPA policy engine is enabled.
        url: URL of the OPA server.
        policy_path: Path to the policy decision endpoint.
        timeout: HTTP request timeout in seconds.
    """

    enabled: bool = False
    url: str = "http://localhost:8181"
    policy_path: str = "v1/data/mcp/authz/allow"
    timeout: float = 5.0


@dataclass
class RoleAssignment:
    """A single role assignment configuration.

    Attributes:
        principal: Principal ID (e.g., "user:admin@company.com", "group:platform-engineering").
        role: Role name (e.g., "admin", "developer").
        scope: Scope of the assignment (e.g., "global", "tenant:data-team").
    """

    principal: str
    role: str
    scope: str = "global"


@dataclass
class StorageConfig:
    """Storage backend configuration for auth data.

    Attributes:
        driver: Storage driver ("memory", "sqlite", "postgresql").
        path: Path for SQLite database file (only for sqlite driver).
        host: Database host (only for postgresql driver).
        port: Database port (only for postgresql driver).
        database: Database name (only for postgresql driver).
        user: Database user (only for postgresql driver).
        password: Database password (only for postgresql driver).
        min_connections: Minimum pool connections (only for postgresql driver).
        max_connections: Maximum pool connections (only for postgresql driver).
    """

    driver: str = "memory"  # memory, sqlite, postgresql

    # SQLite options
    path: str = "data/auth.db"

    # PostgreSQL options
    host: str = "localhost"
    port: int = 5432
    database: str = "mcp_hangar"
    user: str = "mcp_hangar"
    password: str = ""
    min_connections: int = 2
    max_connections: int = 10


@dataclass
class RateLimitConfig:
    """Rate limiting configuration for auth attempts.

    Attributes:
        enabled: Whether rate limiting is enabled.
        max_attempts: Maximum failed attempts per window.
        window_seconds: Time window for counting attempts.
        lockout_seconds: How long to lock out after exceeding limit.
    """

    enabled: bool = True
    max_attempts: int = 10
    window_seconds: int = 60
    lockout_seconds: int = 300


@dataclass
class AuthConfig:
    """Authentication and authorization configuration.

    This is the main configuration container for all auth settings.

    Authentication is OPT-IN by default (enabled=False). Set enabled=True
    in your configuration to activate authentication.

    Attributes:
        enabled: Master switch for auth (if False, all requests are allowed). Default: False (opt-in).
        allow_anonymous: If True, allow unauthenticated requests as anonymous.
        storage: Storage backend configuration.
        rate_limit: Rate limiting configuration.
        api_key: API key authentication configuration.
        oidc: OIDC/JWT authentication configuration.
        opa: OPA policy engine configuration.
        role_assignments: Static role assignments from configuration.
    """

    enabled: bool = False  # OPT-IN: auth disabled by default
    allow_anonymous: bool = False

    storage: StorageConfig = field(default_factory=StorageConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)

    api_key: ApiKeyAuthConfig = field(default_factory=ApiKeyAuthConfig)
    oidc: OIDCAuthConfig = field(default_factory=OIDCAuthConfig)
    opa: OPAConfig = field(default_factory=OPAConfig)

    role_assignments: list[RoleAssignment] = field(default_factory=list)


def parse_auth_config(config_dict: dict[str, Any] | None) -> AuthConfig:
    """Parse auth configuration from dictionary.

    Args:
        config_dict: The 'auth' section of the configuration file.

    Returns:
        Parsed AuthConfig object with defaults for missing values.
    """
    if config_dict is None:
        return AuthConfig()

    # Parse storage config
    storage_dict = config_dict.get("storage", {})
    storage_config = StorageConfig(
        driver=storage_dict.get("driver", "memory"),
        path=storage_dict.get("path", "data/auth.db"),
        host=storage_dict.get("host", "localhost"),
        port=storage_dict.get("port", 5432),
        database=storage_dict.get("database", "mcp_hangar"),
        user=storage_dict.get("user", "mcp_hangar"),
        password=storage_dict.get("password", ""),
        min_connections=storage_dict.get("min_connections", 2),
        max_connections=storage_dict.get("max_connections", 10),
    )

    # Parse rate limit config
    rate_limit_dict = config_dict.get("rate_limit", {})
    rate_limit_config = RateLimitConfig(
        enabled=rate_limit_dict.get("enabled", True),
        max_attempts=rate_limit_dict.get("max_attempts", 10),
        window_seconds=rate_limit_dict.get("window_seconds", 60),
        lockout_seconds=rate_limit_dict.get("lockout_seconds", 300),
    )

    # Parse API key config
    api_key_dict = config_dict.get("api_key", {})
    api_key_config = ApiKeyAuthConfig(
        enabled=api_key_dict.get("enabled", True),
        header_name=api_key_dict.get("header_name", "X-API-Key"),
    )

    # Parse OIDC config
    oidc_dict = config_dict.get("oidc", {})
    oidc_config = OIDCAuthConfig(
        enabled=oidc_dict.get("enabled", False),
        issuer=oidc_dict.get("issuer", ""),
        audience=oidc_dict.get("audience", ""),
        jwks_uri=oidc_dict.get("jwks_uri"),
        client_id=oidc_dict.get("client_id"),
        subject_claim=oidc_dict.get("subject_claim", "sub"),
        groups_claim=oidc_dict.get("groups_claim", "groups"),
        tenant_claim=oidc_dict.get("tenant_claim", "tenant_id"),
        email_claim=oidc_dict.get("email_claim", "email"),
    )

    # Parse OPA config
    opa_dict = config_dict.get("opa", {})
    opa_config = OPAConfig(
        enabled=opa_dict.get("enabled", False),
        url=opa_dict.get("url", "http://localhost:8181"),
        policy_path=opa_dict.get("policy_path", "v1/data/mcp/authz/allow"),
        timeout=opa_dict.get("timeout", 5.0),
    )

    # Parse role assignments
    role_assignments: list[RoleAssignment] = []
    for assignment_dict in config_dict.get("role_assignments", []):
        if isinstance(assignment_dict, dict):
            role_assignments.append(
                RoleAssignment(
                    principal=assignment_dict.get("principal", ""),
                    role=assignment_dict.get("role", ""),
                    scope=assignment_dict.get("scope", "global"),
                )
            )

    return AuthConfig(
        enabled=config_dict.get("enabled", False),  # OPT-IN: default to disabled
        allow_anonymous=config_dict.get("allow_anonymous", False),
        storage=storage_config,
        rate_limit=rate_limit_config,
        api_key=api_key_config,
        oidc=oidc_config,
        opa=opa_config,
        role_assignments=role_assignments,
    )


def get_default_auth_config() -> AuthConfig:
    """Get default auth configuration.

    Returns a disabled auth configuration suitable for development
    where authentication is not required.

    Returns:
        AuthConfig with auth disabled.
    """
    return AuthConfig(enabled=False, allow_anonymous=True)


# Example configuration (for documentation):
EXAMPLE_AUTH_CONFIG = """
auth:
  enabled: true
  allow_anonymous: false

  api_key:
    enabled: true
    header_name: X-API-Key

  oidc:
    enabled: true
    issuer: https://auth.company.com
    audience: mcp-hangar
    # jwks_uri auto-discovered from issuer if not specified
    groups_claim: groups
    tenant_claim: org_id

  opa:
    enabled: false  # Use built-in RBAC by default
    url: http://opa:8181
    policy_path: v1/data/mcp/authz/allow

  role_assignments:
    # Bootstrap admin
    - principal: "user:admin@company.com"
      role: admin
      scope: global

    # Platform team
    - principal: "group:platform-engineering"
      role: provider-admin
      scope: global

    # Data team - scoped to their tenant
    - principal: "group:data-science"
      role: developer
      scope: "tenant:data-team"
"""
