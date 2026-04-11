"""Authentication infrastructure — moved to enterprise.

This module is a compatibility shim. The actual implementations
have been moved to ``enterprise/auth/infrastructure/`` under BSL 1.1.

Import directly from enterprise when available:

    from enterprise.auth.infrastructure.api_key_authenticator import (
        ApiKeyAuthenticator, InMemoryApiKeyStore,
    )
"""

try:
    from enterprise.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator, InMemoryApiKeyStore
    from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, JWTAuthenticator, OIDCConfig
    from enterprise.auth.infrastructure.middleware import AuthContext, AuthenticationMiddleware, AuthorizationMiddleware
    from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer
    from enterprise.auth.infrastructure.rate_limiter import AuthRateLimitConfig, AuthRateLimiter
    from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer

    __all__ = [
        "ApiKeyAuthenticator",
        "InMemoryApiKeyStore",
        "JWTAuthenticator",
        "JWKSTokenValidator",
        "OIDCConfig",
        "AuthenticationMiddleware",
        "AuthorizationMiddleware",
        "AuthContext",
        "AuthRateLimiter",
        "AuthRateLimitConfig",
        "RBACAuthorizer",
        "InMemoryRoleStore",
        "OPAAuthorizer",
    ]
except ImportError:
    __all__ = []
