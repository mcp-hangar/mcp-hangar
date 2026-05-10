"""Authentication infrastructure — moved to enterprise.

Actual implementations live in ``enterprise/auth/infrastructure/`` under BSL 1.1.
This module provides lazy re-exports via importlib for backward compatibility.
"""

import importlib

_ENTERPRISE_AUTH_SYMBOLS: dict[str, str] = {
    "ApiKeyAuthenticator": "enterprise.auth.infrastructure.api_key_authenticator",
    "InMemoryApiKeyStore": "enterprise.auth.infrastructure.api_key_authenticator",
    "JWTAuthenticator": "enterprise.auth.infrastructure.jwt_authenticator",
    "JWKSTokenValidator": "enterprise.auth.infrastructure.jwt_authenticator",
    "OIDCConfig": "enterprise.auth.infrastructure.jwt_authenticator",
    "AuthenticationMiddleware": "enterprise.auth.infrastructure.middleware",
    "AuthorizationMiddleware": "enterprise.auth.infrastructure.middleware",
    "AuthContext": "enterprise.auth.infrastructure.middleware",
    "AuthRateLimiter": "enterprise.auth.infrastructure.rate_limiter",
    "AuthRateLimitConfig": "enterprise.auth.infrastructure.rate_limiter",
    "RBACAuthorizer": "enterprise.auth.infrastructure.rbac_authorizer",
    "InMemoryRoleStore": "enterprise.auth.infrastructure.rbac_authorizer",
    "OPAAuthorizer": "enterprise.auth.infrastructure.opa_authorizer",
}

__all__: list[str] = list(_ENTERPRISE_AUTH_SYMBOLS)


def __getattr__(name: str):  # noqa: ANN001
    module_name = _ENTERPRISE_AUTH_SYMBOLS.get(name)
    if module_name is not None:
        try:
            return getattr(importlib.import_module(module_name), name)
        except ImportError as err:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r} (enterprise not installed)") from err
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
