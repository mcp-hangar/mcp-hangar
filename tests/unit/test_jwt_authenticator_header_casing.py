"""Regression: JWTAuthenticator must read the Authorization header case-insensitively.

The HTTP auth middleware normalizes header names to lowercase (ASGI headers are
already lowercase), so a case-sensitive ``get("Authorization")`` made
``supports()`` return False for every real request -- the bearer token was never
routed to the JWT authenticator and hangar answered ``auth_method: none``,
breaking OIDC/JWT auth over the HTTP surface entirely. Surfaced by the T2 live
harness (mcp-hangar/mcp-hangar#471).
"""

from __future__ import annotations

import time
from unittest.mock import Mock

from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig
from mcp_hangar.domain.contracts.authentication import AuthRequest


def _authenticator(validator: Mock | None = None) -> JWTAuthenticator:
    return JWTAuthenticator(
        OIDCConfig(issuer="https://issuer.example", audience="mcp-hangar"),
        validator or Mock(),
    )


def test_supports_accepts_lowercase_authorization_header() -> None:
    auth = _authenticator()
    assert auth.supports(AuthRequest(headers={"Authorization": "Bearer x"}, source_ip="1"))
    # ASGI / the HTTP middleware lowercases header names; this is the real case.
    assert auth.supports(AuthRequest(headers={"authorization": "Bearer x"}, source_ip="1"))


def test_supports_ignores_non_bearer() -> None:
    auth = _authenticator()
    assert not auth.supports(AuthRequest(headers={"authorization": "Basic x"}, source_ip="1"))
    assert not auth.supports(AuthRequest(headers={}, source_ip="1"))


def test_authenticate_reads_lowercase_authorization_header() -> None:
    now = int(time.time())
    validator = Mock()
    validator.validate.return_value = {
        "iss": "https://issuer.example",
        "sub": "user-1",
        "aud": "mcp-hangar",
        "groups": [],
        "iat": now,
        "exp": now + 300,
    }
    auth = _authenticator(validator)

    auth.authenticate(AuthRequest(headers={"authorization": "Bearer tok"}, source_ip="1"))

    # The token was extracted from the lowercase header and passed to the validator.
    validator.validate.assert_called_once_with("tok")
