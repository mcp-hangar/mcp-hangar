"""Authentication infrastructure.

Provides lazy re-exports of auth implementations via importlib.
"""

import importlib

_AUTH_SYMBOLS: dict[str, str] = {
    "ApiKeyAuthenticator": "mcp_hangar.auth.infrastructure.api_key_authenticator",
    "InMemoryApiKeyStore": "mcp_hangar.auth.infrastructure.api_key_authenticator",
    "JWTAuthenticator": "mcp_hangar.auth.infrastructure.jwt_authenticator",
    "JWKSTokenValidator": "mcp_hangar.auth.infrastructure.jwt_authenticator",
    "OIDCConfig": "mcp_hangar.auth.infrastructure.jwt_authenticator",
    "AuthenticationMiddleware": "mcp_hangar.auth.infrastructure.middleware",
    "AuthorizationMiddleware": "mcp_hangar.auth.infrastructure.middleware",
    "AuthContext": "mcp_hangar.auth.infrastructure.middleware",
    "AuthRateLimiter": "mcp_hangar.auth.infrastructure.rate_limiter",
    "AuthRateLimitConfig": "mcp_hangar.auth.infrastructure.rate_limiter",
    "RBACAuthorizer": "mcp_hangar.auth.infrastructure.rbac_authorizer",
    "InMemoryRoleStore": "mcp_hangar.auth.infrastructure.rbac_authorizer",
    "OPAAuthorizer": "mcp_hangar.auth.infrastructure.opa_authorizer",
}

__all__: list[str] = list(_AUTH_SYMBOLS)


def __getattr__(name: str):  # noqa: ANN001
    module_name = _AUTH_SYMBOLS.get(name)
    if module_name is not None:
        try:
            return getattr(importlib.import_module(module_name), name)
        except ImportError as err:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r} (auth module not available)") from err
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
