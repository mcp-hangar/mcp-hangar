"""Authentication infrastructure implementations.

This module provides concrete implementations of authentication contracts
defined in the domain layer.
"""

from .api_key_authenticator import ApiKeyAuthenticator, InMemoryApiKeyStore
from .jwt_authenticator import JWKSTokenValidator, JWTAuthenticator, OIDCConfig
from .middleware import AuthContext, AuthenticationMiddleware, AuthorizationMiddleware
from .opa_authorizer import OPAAuthorizer
from .rate_limiter import AuthRateLimitConfig, AuthRateLimiter
from .rbac_authorizer import InMemoryRoleStore, RBACAuthorizer

# Persistent stores - imported lazily to avoid dependency issues
# Use: from mcp_hangar.infrastructure.auth.sqlite_store import SQLiteApiKeyStore
# Use: from mcp_hangar.infrastructure.auth.postgres_store import PostgresApiKeyStore

__all__ = [
    # API Key authentication
    "ApiKeyAuthenticator",
    "InMemoryApiKeyStore",
    # JWT/OIDC authentication
    "JWTAuthenticator",
    "JWKSTokenValidator",
    "OIDCConfig",
    # Middleware
    "AuthenticationMiddleware",
    "AuthorizationMiddleware",
    "AuthContext",
    # Rate limiting
    "AuthRateLimiter",
    "AuthRateLimitConfig",
    # Authorization
    "RBACAuthorizer",
    "InMemoryRoleStore",
    "OPAAuthorizer",
]
