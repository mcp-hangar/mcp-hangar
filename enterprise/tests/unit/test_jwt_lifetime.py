"""Tests for JWT token lifetime enforcement.

Tests the max_token_lifetime feature that prevents excessively long-lived
JWT tokens from being accepted, even if they have valid signatures.
"""

import time
from typing import Any

import pytest

from mcp_hangar.domain.contracts.authentication import AuthRequest, ITokenValidator
from mcp_hangar.domain.exceptions import InvalidCredentialsError, TokenLifetimeExceededError
from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig
from enterprise.auth.config import OIDCAuthConfig, parse_auth_config


class MockTokenValidator(ITokenValidator):
    """Mock token validator that returns preconfigured claims."""

    def __init__(self, claims: dict[str, Any]):
        """Initialize with claims to return from validate().

        Args:
            claims: Dictionary of JWT claims to return.
        """
        self._claims = claims

    def validate(self, token: str) -> dict:
        """Return the preconfigured claims.

        Args:
            token: The JWT token (ignored in mock).

        Returns:
            The preconfigured claims dict.
        """
        return self._claims


def make_claims(
    iat_offset: int = -1800,
    lifetime: int = 3600,
    sub: str = "user:test",
    iss: str = "https://auth.example.com",
    aud: str = "mcp-hangar",
) -> dict[str, Any]:
    """Factory for creating JWT claims with controlled iat/exp.

    Args:
        iat_offset: Seconds offset from current time for iat (negative = past).
        lifetime: Token lifetime in seconds (exp - iat).
        sub: Subject claim.
        iss: Issuer claim.
        aud: Audience claim.

    Returns:
        Dictionary of JWT claims.
    """
    now = int(time.time())
    iat = now + iat_offset
    exp = iat + lifetime

    return {
        "sub": sub,
        "iss": iss,
        "aud": aud,
        "iat": iat,
        "exp": exp,
        "email": "test@example.com",
    }


class TestJWTLifetimeEnforcement:
    """Tests for JWT lifetime enforcement in JWTAuthenticator."""

    def test_token_within_lifetime_accepted(self):
        """Token with (exp - iat) = 1800s, max_token_lifetime=3600 should be accepted."""
        claims = make_claims(iat_offset=-900, lifetime=1800)
        validator = MockTokenValidator(claims)

        config = OIDCConfig(
            issuer="https://auth.example.com",
            audience="mcp-hangar",
            max_token_lifetime=3600,
        )

        authenticator = JWTAuthenticator(config, validator)
        request = AuthRequest(
            headers={"Authorization": "Bearer valid-token"},
            source_ip="192.168.1.1",
        )

        # Should succeed - token lifetime is within limit
        principal = authenticator.authenticate(request)
        assert principal.id.value == "user:test"

    def test_token_exceeding_lifetime_rejected(self):
        """Token with (exp - iat) = 7200s, max_token_lifetime=3600 should be rejected."""
        claims = make_claims(iat_offset=-900, lifetime=7200)
        validator = MockTokenValidator(claims)

        config = OIDCConfig(
            issuer="https://auth.example.com",
            audience="mcp-hangar",
            max_token_lifetime=3600,
        )

        authenticator = JWTAuthenticator(config, validator)
        request = AuthRequest(
            headers={"Authorization": "Bearer long-lived-token"},
            source_ip="192.168.1.1",
        )

        # Should raise TokenLifetimeExceededError
        with pytest.raises(TokenLifetimeExceededError) as exc_info:
            authenticator.authenticate(request)

        assert "7200" in str(exc_info.value.message)
        assert "3600" in str(exc_info.value.message)
        assert exc_info.value.actual_lifetime == 7200
        assert exc_info.value.max_lifetime == 3600

    def test_token_exactly_at_lifetime_accepted(self):
        """Token with (exp - iat) = 3600s exactly, max_token_lifetime=3600 should be accepted."""
        claims = make_claims(iat_offset=-1800, lifetime=3600)
        validator = MockTokenValidator(claims)

        config = OIDCConfig(
            issuer="https://auth.example.com",
            audience="mcp-hangar",
            max_token_lifetime=3600,
        )

        authenticator = JWTAuthenticator(config, validator)
        request = AuthRequest(
            headers={"Authorization": "Bearer exact-lifetime-token"},
            source_ip="192.168.1.1",
        )

        # Should succeed - exactly at the boundary (<=)
        principal = authenticator.authenticate(request)
        assert principal.id.value == "user:test"

    def test_token_exceeding_lifetime_by_one_second_rejected(self):
        """Token with (exp - iat) = 3601s, max_token_lifetime=3600 should be rejected."""
        claims = make_claims(iat_offset=-1800, lifetime=3601)
        validator = MockTokenValidator(claims)

        config = OIDCConfig(
            issuer="https://auth.example.com",
            audience="mcp-hangar",
            max_token_lifetime=3600,
        )

        authenticator = JWTAuthenticator(config, validator)
        request = AuthRequest(
            headers={"Authorization": "Bearer one-second-over-token"},
            source_ip="192.168.1.1",
        )

        # Should raise TokenLifetimeExceededError
        with pytest.raises(TokenLifetimeExceededError) as exc_info:
            authenticator.authenticate(request)

        assert exc_info.value.actual_lifetime == 3601
        assert exc_info.value.max_lifetime == 3600

    def test_token_missing_iat_rejected(self):
        """Token claims with exp but no iat should be rejected with clear error."""
        now = int(time.time())
        claims = {
            "sub": "user:test",
            "iss": "https://auth.example.com",
            "aud": "mcp-hangar",
            "exp": now + 3600,
            # Missing 'iat'
        }
        validator = MockTokenValidator(claims)

        config = OIDCConfig(
            issuer="https://auth.example.com",
            audience="mcp-hangar",
            max_token_lifetime=3600,
        )

        authenticator = JWTAuthenticator(config, validator)
        request = AuthRequest(
            headers={"Authorization": "Bearer missing-iat-token"},
            source_ip="192.168.1.1",
        )

        # Should raise InvalidCredentialsError mentioning 'iat'
        with pytest.raises(InvalidCredentialsError) as exc_info:
            authenticator.authenticate(request)

        assert "iat" in str(exc_info.value.message).lower()

    def test_token_missing_exp_with_lifetime_check(self):
        """Token claims with iat but no exp should be rejected with clear error."""
        now = int(time.time())
        claims = {
            "sub": "user:test",
            "iss": "https://auth.example.com",
            "aud": "mcp-hangar",
            "iat": now - 1800,
            # Missing 'exp'
        }
        validator = MockTokenValidator(claims)

        config = OIDCConfig(
            issuer="https://auth.example.com",
            audience="mcp-hangar",
            max_token_lifetime=3600,
        )

        authenticator = JWTAuthenticator(config, validator)
        request = AuthRequest(
            headers={"Authorization": "Bearer missing-exp-token"},
            source_ip="192.168.1.1",
        )

        # Should raise InvalidCredentialsError mentioning 'exp'
        with pytest.raises(InvalidCredentialsError) as exc_info:
            authenticator.authenticate(request)

        assert "exp" in str(exc_info.value.message).lower()

    def test_default_max_token_lifetime_is_3600(self):
        """OIDCConfig() with no max_token_lifetime specified should default to 3600."""
        config = OIDCConfig(
            issuer="https://auth.example.com",
            audience="mcp-hangar",
        )

        assert config.max_token_lifetime == 3600

    def test_custom_max_token_lifetime_respected(self):
        """OIDCConfig(max_token_lifetime=7200) should allow tokens up to 7200s."""
        claims = make_claims(iat_offset=-1800, lifetime=7200)
        validator = MockTokenValidator(claims)

        config = OIDCConfig(
            issuer="https://auth.example.com",
            audience="mcp-hangar",
            max_token_lifetime=7200,
        )

        authenticator = JWTAuthenticator(config, validator)
        request = AuthRequest(
            headers={"Authorization": "Bearer custom-lifetime-token"},
            source_ip="192.168.1.1",
        )

        # Should succeed - token lifetime is within custom limit
        principal = authenticator.authenticate(request)
        assert principal.id.value == "user:test"

    def test_lifetime_check_disabled_when_zero(self):
        """OIDCConfig(max_token_lifetime=0) should disable the check."""
        # Token with extremely long lifetime
        claims = make_claims(iat_offset=-1800, lifetime=86400 * 365)  # 1 year
        validator = MockTokenValidator(claims)

        config = OIDCConfig(
            issuer="https://auth.example.com",
            audience="mcp-hangar",
            max_token_lifetime=0,  # Disabled
        )

        authenticator = JWTAuthenticator(config, validator)
        request = AuthRequest(
            headers={"Authorization": "Bearer year-long-token"},
            source_ip="192.168.1.1",
        )

        # Should succeed - lifetime check is disabled
        principal = authenticator.authenticate(request)
        assert principal.id.value == "user:test"

    def test_error_message_is_specific_not_generic(self):
        """Error message should contain 'lifetime exceeds maximum', not generic text."""
        claims = make_claims(iat_offset=-900, lifetime=7200)
        validator = MockTokenValidator(claims)

        config = OIDCConfig(
            issuer="https://auth.example.com",
            audience="mcp-hangar",
            max_token_lifetime=3600,
        )

        authenticator = JWTAuthenticator(config, validator)
        request = AuthRequest(
            headers={"Authorization": "Bearer long-lived-token"},
            source_ip="192.168.1.1",
        )

        with pytest.raises(TokenLifetimeExceededError) as exc_info:
            authenticator.authenticate(request)

        message = str(exc_info.value.message).lower()
        assert "lifetime" in message
        assert "exceeds" in message or "maximum" in message
        # Should NOT be generic "invalid jwt token"
        assert "invalid jwt token" not in message


class TestOIDCAuthConfigLifetime:
    """Tests for max_token_lifetime_seconds in OIDCAuthConfig and parse_auth_config."""

    def test_default_max_token_lifetime_seconds(self):
        """OIDCAuthConfig should default max_token_lifetime_seconds to 3600."""
        config = OIDCAuthConfig()
        assert config.max_token_lifetime_seconds == 3600

    def test_parse_auth_config_with_max_token_lifetime(self):
        """parse_auth_config with oidc.max_token_lifetime_seconds=7200 should parse correctly."""
        config_dict = {
            "enabled": True,
            "oidc": {
                "enabled": True,
                "issuer": "https://auth.example.com",
                "audience": "mcp-hangar",
                "max_token_lifetime_seconds": 7200,
            },
        }

        auth_config = parse_auth_config(config_dict)
        assert auth_config.oidc.max_token_lifetime_seconds == 7200

    def test_parse_auth_config_default_lifetime(self):
        """parse_auth_config with no lifetime field should default to 3600."""
        config_dict = {
            "enabled": True,
            "oidc": {
                "enabled": True,
                "issuer": "https://auth.example.com",
                "audience": "mcp-hangar",
                # No max_token_lifetime_seconds
            },
        }

        auth_config = parse_auth_config(config_dict)
        assert auth_config.oidc.max_token_lifetime_seconds == 3600
