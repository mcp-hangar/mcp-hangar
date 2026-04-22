"""Batch 2: Enterprise auth infrastructure tests.

Covers enterprise/auth/cli.py, enterprise/auth/infrastructure/jwt_authenticator.py,
enterprise/auth/infrastructure/opa_authorizer.py, and enterprise/auth/bootstrap.py.

Target: ~321 missed statements.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, UTC
from dataclasses import dataclass
from typing import Any
from unittest import mock
from unittest.mock import MagicMock, Mock, patch, PropertyMock, ANY

import pytest

from mcp_hangar.domain.contracts.authentication import AuthRequest, ITokenValidator, ApiKeyMetadata
from mcp_hangar.domain.contracts.authorization import AuthorizationRequest, AuthorizationResult
from mcp_hangar.domain.exceptions import (
    ExpiredCredentialsError,
    InvalidCredentialsError,
    TokenLifetimeExceededError,
)
from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_principal(
    subject: str = "user123",
    tenant_id: str | None = None,
    groups: frozenset[str] | None = None,
) -> Principal:
    return Principal(
        id=PrincipalId(subject),
        type=PrincipalType.USER,
        tenant_id=tenant_id,
        groups=groups or frozenset(),
    )


def _make_auth_request(headers: dict[str, str] | None = None, source_ip: str = "127.0.0.1") -> AuthRequest:
    return AuthRequest(
        headers=headers or {},
        source_ip=source_ip,
    )


def _make_authz_request(
    principal: Principal | None = None,
    action: str = "invoke",
    resource_type: str = "tool",
    resource_id: str = "calculator",
    context: dict[str, Any] | None = None,
) -> AuthorizationRequest:
    return AuthorizationRequest(
        principal=principal or _make_principal(),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        context=context or {},
    )


# ===========================================================================
# JWT Authenticator tests
# ===========================================================================


class TestOIDCConfig:
    """Test OIDCConfig dataclass defaults and field mappings."""

    def test_defaults(self):
        from enterprise.auth.infrastructure.jwt_authenticator import OIDCConfig

        config = OIDCConfig(issuer="https://auth.example.com", audience="my-api")
        assert config.issuer == "https://auth.example.com"
        assert config.audience == "my-api"
        assert config.jwks_uri is None
        assert config.client_id is None
        assert config.subject_claim == "sub"
        assert config.groups_claim == "groups"
        assert config.tenant_claim == "tenant_id"
        assert config.email_claim == "email"
        assert config.max_token_lifetime == 3600

    def test_custom_claims(self):
        from enterprise.auth.infrastructure.jwt_authenticator import OIDCConfig

        config = OIDCConfig(
            issuer="https://x",
            audience="y",
            subject_claim="user_id",
            groups_claim="roles",
            tenant_claim="org",
            email_claim="mail",
            max_token_lifetime=7200,
        )
        assert config.subject_claim == "user_id"
        assert config.groups_claim == "roles"
        assert config.tenant_claim == "org"
        assert config.email_claim == "mail"
        assert config.max_token_lifetime == 7200


class TestJWTAuthenticatorSupports:
    """Test JWTAuthenticator.supports method."""

    def test_supports_bearer_header(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Bearer eyJ..."})
        assert authn.supports(request) is True

    def test_does_not_support_basic_auth(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Basic dXNlcjpwYXNz"})
        assert authn.supports(request) is False

    def test_does_not_support_missing_header(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({})
        assert authn.supports(request) is False


class TestJWTAuthenticatorAuthenticate:
    """Test JWTAuthenticator.authenticate method."""

    def test_missing_bearer_raises(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Basic dXNlcjpwYXNz"})
        with pytest.raises(InvalidCredentialsError, match="Missing Bearer token"):
            authn.authenticate(request)

    def test_empty_bearer_token_raises(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Bearer "})
        with pytest.raises(InvalidCredentialsError, match="Empty Bearer token"):
            authn.authenticate(request)

    def test_successful_authenticate(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=0)
        validator = Mock(spec=ITokenValidator)
        validator.validate.return_value = {
            "sub": "user:alice",
            "groups": ["admin", "dev"],
            "tenant_id": "acme",
            "email": "alice@acme.com",
            "iss": "https://x",
            "iat": 1000,
            "exp": 2000,
        }
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Bearer valid_token"})
        principal = authn.authenticate(request)

        assert principal.id.value == "user:alice"
        assert principal.type == PrincipalType.USER
        assert principal.tenant_id == "acme"
        assert "admin" in principal.groups
        assert "dev" in principal.groups
        assert principal.metadata["email"] == "alice@acme.com"
        assert principal.metadata["issuer"] == "https://x"

    def test_missing_subject_claim_raises(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=0)
        validator = Mock(spec=ITokenValidator)
        validator.validate.return_value = {"groups": [], "iss": "https://x"}
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Bearer valid_token"})
        with pytest.raises(InvalidCredentialsError, match="Missing sub claim"):
            authn.authenticate(request)

    def test_groups_as_string_converted_to_list(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=0)
        validator = Mock(spec=ITokenValidator)
        validator.validate.return_value = {"sub": "user1", "groups": "single_group"}
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Bearer tok"})
        principal = authn.authenticate(request)
        assert "single_group" in principal.groups

    def test_empty_groups_default(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=0)
        validator = Mock(spec=ITokenValidator)
        validator.validate.return_value = {"sub": "user1"}
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Bearer tok"})
        principal = authn.authenticate(request)
        assert principal.groups == frozenset()

    def test_lifetime_enforcement_called(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=100)
        validator = Mock(spec=ITokenValidator)
        validator.validate.return_value = {
            "sub": "user1",
            "iat": 1000,
            "exp": 1200,  # lifetime = 200 > max 100
        }
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Bearer tok"})
        with pytest.raises(TokenLifetimeExceededError):
            authn.authenticate(request)


class TestJWTLifetimeEnforcement:
    """Test _enforce_token_lifetime edge cases."""

    def test_disabled_when_max_lifetime_zero(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=0)
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        # Should not raise even without iat/exp claims
        authn._enforce_token_lifetime({})

    def test_disabled_when_max_lifetime_negative(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=-1)
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)
        authn._enforce_token_lifetime({})

    def test_missing_iat_raises(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=3600)
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        with pytest.raises(InvalidCredentialsError, match="iat"):
            authn._enforce_token_lifetime({"exp": 9999})

    def test_missing_exp_raises(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=3600)
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        with pytest.raises(InvalidCredentialsError, match="exp"):
            authn._enforce_token_lifetime({"iat": 1000})


class TestJWKSTokenValidator:
    """Test JWKSTokenValidator."""

    def test_pyjwt_not_installed_raises(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = JWKSTokenValidator(config)

        with patch.dict("sys.modules", {"jwt": None}):
            with pytest.raises(InvalidCredentialsError, match="PyJWT"):
                validator.validate("some.token.here")

    def test_expired_signature_raises_expired_credentials(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig
        import jwt as real_jwt

        config = OIDCConfig(issuer="https://x", audience="y", jwks_uri="https://x/.well-known/jwks.json")
        validator = JWKSTokenValidator(config)

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_signing_key.key = "fake_key"
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key
        validator._jwks_client = mock_jwks_client

        with patch("jwt.decode", side_effect=real_jwt.ExpiredSignatureError("expired")):
            with pytest.raises(ExpiredCredentialsError, match="expired"):
                validator.validate("some.token.here")

    def test_invalid_audience_raises_invalid_credentials(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig
        import jwt as real_jwt

        config = OIDCConfig(issuer="https://x", audience="y", jwks_uri="https://x/jwks")
        validator = JWKSTokenValidator(config)

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_signing_key.key = "fake_key"
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key
        validator._jwks_client = mock_jwks_client

        with patch("jwt.decode", side_effect=real_jwt.InvalidAudienceError("bad aud")):
            with pytest.raises(InvalidCredentialsError, match="audience"):
                validator.validate("some.token.here")

    def test_invalid_issuer_raises_invalid_credentials(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig
        import jwt as real_jwt

        config = OIDCConfig(issuer="https://x", audience="y", jwks_uri="https://x/jwks")
        validator = JWKSTokenValidator(config)

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_signing_key.key = "fake_key"
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key
        validator._jwks_client = mock_jwks_client

        with patch("jwt.decode", side_effect=real_jwt.InvalidIssuerError("bad iss")):
            with pytest.raises(InvalidCredentialsError, match="issuer"):
                validator.validate("some.token.here")

    def test_generic_invalid_token_raises_invalid_credentials(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig
        import jwt as real_jwt

        config = OIDCConfig(issuer="https://x", audience="y", jwks_uri="https://x/jwks")
        validator = JWKSTokenValidator(config)

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_signing_key.key = "fake_key"
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key
        validator._jwks_client = mock_jwks_client

        with patch("jwt.decode", side_effect=real_jwt.InvalidTokenError("bad token")):
            with pytest.raises(InvalidCredentialsError, match="Invalid JWT token"):
                validator.validate("some.token.here")

    def test_successful_validation_returns_claims(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", jwks_uri="https://x/jwks")
        validator = JWKSTokenValidator(config)

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_signing_key.key = "fake_key"
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key
        validator._jwks_client = mock_jwks_client

        expected_claims = {"sub": "user1", "iss": "https://x", "aud": "y"}
        with patch("jwt.decode", return_value=expected_claims):
            result = validator.validate("some.token.here")
            assert result == expected_claims


class TestJWKSTokenValidatorInitClient:
    """Test _init_jwks_client with OIDC discovery."""

    def test_with_explicit_jwks_uri(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

        config = OIDCConfig(
            issuer="https://auth.example.com",
            audience="y",
            jwks_uri="https://auth.example.com/custom/jwks",
        )
        validator = JWKSTokenValidator(config)

        with patch("jwt.PyJWKClient") as mock_client_cls:
            validator._init_jwks_client()
            mock_client_cls.assert_called_once_with("https://auth.example.com/custom/jwks")
            assert validator._jwks_uri == "https://auth.example.com/custom/jwks"

    def test_oidc_discovery_success(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

        config = OIDCConfig(issuer="https://auth.example.com", audience="y")
        validator = JWKSTokenValidator(config)

        mock_response = MagicMock()
        mock_response.json.return_value = {"jwks_uri": "https://auth.example.com/keys"}
        mock_response.raise_for_status.return_value = None

        with patch("httpx.get", return_value=mock_response):
            with patch("jwt.PyJWKClient") as mock_client_cls:
                validator._init_jwks_client()
                mock_client_cls.assert_called_once_with("https://auth.example.com/keys")

    def test_oidc_discovery_no_jwks_uri_raises(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

        config = OIDCConfig(issuer="https://auth.example.com", audience="y")
        validator = JWKSTokenValidator(config)

        mock_response = MagicMock()
        mock_response.json.return_value = {}  # no jwks_uri
        mock_response.raise_for_status.return_value = None

        with patch("httpx.get", return_value=mock_response):
            with pytest.raises(InvalidCredentialsError, match="did not return jwks_uri"):
                validator._init_jwks_client()

    def test_oidc_discovery_http_error_raises(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig
        import httpx

        config = OIDCConfig(issuer="https://auth.example.com", audience="y")
        validator = JWKSTokenValidator(config)

        with patch("httpx.get", side_effect=httpx.HTTPError("connection refused")):
            with pytest.raises(InvalidCredentialsError, match="discover OIDC"):
                validator._init_jwks_client()

    def test_non_https_issuer_logs_warning(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

        config = OIDCConfig(
            issuer="http://insecure-auth.example.com",
            audience="y",
            jwks_uri="https://auth.example.com/jwks",
        )
        validator = JWKSTokenValidator(config)

        with patch("jwt.PyJWKClient"):
            validator._init_jwks_client()
            # No exception -- just a warning logged

    def test_non_https_jwks_uri_discovered(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

        config = OIDCConfig(issuer="https://auth.example.com", audience="y")
        validator = JWKSTokenValidator(config)

        mock_response = MagicMock()
        mock_response.json.return_value = {"jwks_uri": "http://insecure/jwks"}
        mock_response.raise_for_status.return_value = None

        with patch("httpx.get", return_value=mock_response):
            with patch("jwt.PyJWKClient") as mock_client_cls:
                validator._init_jwks_client()
                # Should still proceed but with warning logged
                mock_client_cls.assert_called_once_with("http://insecure/jwks")

    def test_import_error_raises_invalid_credentials(self):
        from enterprise.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

        config = OIDCConfig(issuer="https://auth.example.com", audience="y")
        validator = JWKSTokenValidator(config)

        with patch.dict("sys.modules", {"httpx": None}):
            with pytest.raises(InvalidCredentialsError, match="additional libraries"):
                validator._init_jwks_client()


class TestStaticSecretTokenValidator:
    """Test StaticSecretTokenValidator."""

    def test_valid_token_with_hs256(self):
        from enterprise.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator
        import jwt

        secret = "test-secret-key"
        validator = StaticSecretTokenValidator(secret)
        now = int(time.time())
        token = jwt.encode({"sub": "user1", "iat": now, "exp": now + 3600}, secret, algorithm="HS256")

        claims = validator.validate(token)
        assert claims["sub"] == "user1"

    def test_expired_token_raises(self):
        from enterprise.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator
        import jwt

        secret = "test-secret-key"
        validator = StaticSecretTokenValidator(secret)
        now = int(time.time())
        token = jwt.encode({"sub": "user1", "iat": now - 7200, "exp": now - 3600}, secret, algorithm="HS256")

        with pytest.raises(ExpiredCredentialsError, match="expired"):
            validator.validate(token)

    def test_invalid_token_raises(self):
        from enterprise.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator

        validator = StaticSecretTokenValidator("correct-secret")
        import jwt

        token = jwt.encode({"sub": "user1"}, "wrong-secret", algorithm="HS256")

        with pytest.raises(InvalidCredentialsError, match="Invalid JWT"):
            validator.validate(token)

    def test_with_issuer_and_audience(self):
        from enterprise.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator
        import jwt

        secret = "test-secret-key"
        validator = StaticSecretTokenValidator(secret, issuer="https://issuer", audience="my-api")
        now = int(time.time())
        token = jwt.encode(
            {"sub": "user1", "iat": now, "exp": now + 3600, "iss": "https://issuer", "aud": "my-api"},
            secret,
            algorithm="HS256",
        )
        claims = validator.validate(token)
        assert claims["sub"] == "user1"
        assert claims["iss"] == "https://issuer"

    def test_with_wrong_issuer_raises(self):
        from enterprise.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator
        import jwt

        secret = "test-secret-key"
        validator = StaticSecretTokenValidator(secret, issuer="https://expected")
        now = int(time.time())
        token = jwt.encode(
            {"sub": "user1", "iat": now, "exp": now + 3600, "iss": "https://other"},
            secret,
            algorithm="HS256",
        )
        with pytest.raises(InvalidCredentialsError, match="Invalid JWT"):
            validator.validate(token)

    def test_without_issuer_audience_skips_verification(self):
        from enterprise.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator
        import jwt

        secret = "test-secret-key"
        # No issuer or audience set -> should not verify them
        validator = StaticSecretTokenValidator(secret)
        now = int(time.time())
        token = jwt.encode({"sub": "user1", "iat": now, "exp": now + 3600}, secret, algorithm="HS256")
        claims = validator.validate(token)
        assert claims["sub"] == "user1"


# ===========================================================================
# OPA Authorizer tests
# ===========================================================================


class TestOPAAuthorizerInit:
    """Test OPAAuthorizer initialization."""

    def test_trailing_slash_stripped_from_url(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181/")
        assert opa._opa_url == "http://localhost:8181"

    def test_leading_slash_stripped_from_policy_path(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181", policy_path="/v1/data/allow")
        assert opa._policy_path == "v1/data/allow"

    def test_timeout_set(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181", timeout=10.0)
        assert opa._timeout == 10.0

    def test_client_initially_none(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")
        assert opa._client is None


class TestOPAAuthorizerEvaluate:
    """Test OPAAuthorizer.evaluate with various error scenarios."""

    def test_httpx_not_installed_denies(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")

        with patch.dict("sys.modules", {"httpx": None}):
            result = opa.evaluate({"principal": {"id": "u1"}})
            assert not result.allowed
            assert "httpx_not_installed" in result.reason

    def test_successful_allow(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer
        import httpx

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": True}
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert result.allowed
        assert result.reason == "opa_policy"

    def test_successful_deny(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer
        import httpx

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": False}
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert not result.allowed
        assert result.reason == "opa_denied"

    def test_missing_result_key_defaults_to_deny(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer
        import httpx

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_response = MagicMock()
        mock_response.json.return_value = {}  # no "result" key
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert not result.allowed
        assert result.reason == "opa_denied"

    def test_connect_error_denies(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer
        import httpx

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.side_effect = httpx.ConnectError("connection refused")
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert not result.allowed
        assert "connection_failed" in result.reason

    def test_timeout_denies(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer
        import httpx

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.side_effect = httpx.TimeoutException("timed out")
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert not result.allowed
        assert "timeout" in result.reason

    def test_http_status_error_denies(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer
        import httpx

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_response = MagicMock()
        mock_response.status_code = 500
        error = httpx.HTTPStatusError("server error", request=MagicMock(), response=mock_response)
        mock_client.post.side_effect = error
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert not result.allowed
        assert "http_500" in result.reason

    def test_generic_exception_denies(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer
        import httpx

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.side_effect = RuntimeError("unexpected")
        opa._client = mock_client

        result = opa.evaluate({"principal": {"id": "u1"}})
        assert not result.allowed
        assert "RuntimeError" in result.reason

    def test_lazy_client_initialization(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer
        import httpx

        opa = OPAAuthorizer("http://localhost:8181", timeout=3.0)
        assert opa._client is None

        mock_response = MagicMock()
        mock_response.json.return_value = {"result": True}
        mock_response.raise_for_status.return_value = None

        with patch("httpx.Client") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.post.return_value = mock_response
            mock_client_cls.return_value = mock_instance

            result = opa.evaluate({"principal": {"id": "u1"}})
            assert result.allowed
            mock_client_cls.assert_called_once_with(timeout=3.0)


class TestOPAAuthorizerBuildInput:
    """Test static build_input method."""

    def test_build_input_structure(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer

        principal = _make_principal(
            subject="user:alice",
            tenant_id="acme",
            groups=frozenset(["admin"]),
        )
        request = _make_authz_request(
            principal=principal,
            action="invoke",
            resource_type="tool",
            resource_id="calc",
            context={"rate_limit": True},
        )

        input_data = OPAAuthorizer.build_input(request)

        assert input_data["principal"]["id"] == "user:alice"
        assert input_data["principal"]["type"] == "user"
        assert input_data["principal"]["tenant_id"] == "acme"
        assert "admin" in input_data["principal"]["groups"]
        assert input_data["action"] == "invoke"
        assert input_data["resource"]["type"] == "tool"
        assert input_data["resource"]["id"] == "calc"
        assert input_data["context"] == {"rate_limit": True}


class TestOPAAuthorizerAuthorize:
    """Test authorize convenience method."""

    def test_delegates_to_build_input_and_evaluate(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer
        import httpx

        opa = OPAAuthorizer("http://localhost:8181")

        mock_client = MagicMock(spec=httpx.Client)
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": True}
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        opa._client = mock_client

        request = _make_authz_request()
        result = opa.authorize(request)
        assert result.allowed


class TestOPAAuthorizerCloseAndContextManager:
    """Test close and context manager."""

    def test_close_when_client_exists(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")
        mock_client = MagicMock()
        opa._client = mock_client

        opa.close()
        mock_client.close.assert_called_once()
        assert opa._client is None

    def test_close_when_no_client(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")
        opa.close()  # Should not raise

    def test_context_manager(self):
        from enterprise.auth.infrastructure.opa_authorizer import OPAAuthorizer

        opa = OPAAuthorizer("http://localhost:8181")
        mock_client = MagicMock()
        opa._client = mock_client

        with opa as ctx:
            assert ctx is opa

        mock_client.close.assert_called_once()


class TestCombinedAuthorizer:
    """Test CombinedAuthorizer dual strategy."""

    def _make_combined(self, require_both: bool = False, opa: Any = "auto"):
        from enterprise.auth.infrastructure.opa_authorizer import CombinedAuthorizer, OPAAuthorizer
        from enterprise.auth.infrastructure.rbac_authorizer import RBACAuthorizer

        rbac = MagicMock(spec=RBACAuthorizer)
        if opa == "auto":
            opa_auth = MagicMock(spec=OPAAuthorizer)
        elif opa is None:
            opa_auth = None
        else:
            opa_auth = opa
        return CombinedAuthorizer(rbac, opa_auth, require_both=require_both), rbac, opa_auth

    def test_no_opa_returns_rbac_result(self):
        combined, rbac, _ = self._make_combined(opa=None)
        rbac.authorize.return_value = AuthorizationResult.allow(reason="rbac_ok")

        result = combined.authorize(_make_authz_request())
        assert result.allowed
        assert result.reason == "rbac_ok"

    def test_require_both_false_rbac_allows_skips_opa(self):
        combined, rbac, opa = self._make_combined(require_both=False)
        rbac.authorize.return_value = AuthorizationResult.allow(reason="rbac_ok", role="admin")

        result = combined.authorize(_make_authz_request())
        assert result.allowed
        assert result.reason == "rbac_ok"
        opa.authorize.assert_not_called()

    def test_require_both_false_rbac_denies_opa_allows(self):
        combined, rbac, opa = self._make_combined(require_both=False)
        rbac.authorize.return_value = AuthorizationResult.deny(reason="rbac_denied")
        opa.authorize.return_value = AuthorizationResult.allow(reason="opa_ok")

        result = combined.authorize(_make_authz_request())
        assert result.allowed
        assert result.reason == "opa_override"

    def test_require_both_false_both_deny(self):
        combined, rbac, opa = self._make_combined(require_both=False)
        rbac_denial = AuthorizationResult.deny(reason="rbac_denied")
        rbac.authorize.return_value = rbac_denial
        opa.authorize.return_value = AuthorizationResult.deny(reason="opa_denied")

        result = combined.authorize(_make_authz_request())
        assert not result.allowed
        assert result.reason == "rbac_denied"  # original RBAC denial returned

    def test_require_both_true_rbac_denies_skips_opa(self):
        combined, rbac, opa = self._make_combined(require_both=True)
        rbac.authorize.return_value = AuthorizationResult.deny(reason="rbac_no")

        result = combined.authorize(_make_authz_request())
        assert not result.allowed
        assert result.reason == "rbac_no"
        opa.authorize.assert_not_called()

    def test_require_both_true_both_allow(self):
        combined, rbac, opa = self._make_combined(require_both=True)
        rbac.authorize.return_value = AuthorizationResult.allow(reason="rbac_ok", role="admin")
        opa.authorize.return_value = AuthorizationResult.allow(reason="opa_ok")

        result = combined.authorize(_make_authz_request())
        assert result.allowed
        assert "rbac_and_opa_allowed" in result.reason

    def test_require_both_true_rbac_allows_opa_denies(self):
        combined, rbac, opa = self._make_combined(require_both=True)
        rbac.authorize.return_value = AuthorizationResult.allow(reason="rbac_ok", role="admin")
        opa.authorize.return_value = AuthorizationResult.deny(reason="opa_denied")

        result = combined.authorize(_make_authz_request())
        assert not result.allowed
        assert "rbac_allowed_but_opa_denied" in result.reason


# ===========================================================================
# CLI tests
# ===========================================================================


class TestAuthCLICreateParser:
    """Test create_auth_parser builds correct argument structure."""

    def test_parser_has_auth_subcommands(self):
        from enterprise.auth.cli import create_auth_parser

        parent = argparse.ArgumentParser()
        subparsers = parent.add_subparsers()
        auth_parser = create_auth_parser(subparsers)

        # Parse each subcommand to ensure they exist
        args = auth_parser.parse_args(["create-key", "--principal", "user:a", "--name", "key1"])
        assert args.principal == "user:a"
        assert args.name == "key1"

    def test_list_keys_subcommand(self):
        from enterprise.auth.cli import create_auth_parser

        parent = argparse.ArgumentParser()
        subparsers = parent.add_subparsers()
        auth_parser = create_auth_parser(subparsers)

        args = auth_parser.parse_args(["list-keys", "--principal", "user:b"])
        assert args.principal == "user:b"

    def test_revoke_key_subcommand_with_yes(self):
        from enterprise.auth.cli import create_auth_parser

        parent = argparse.ArgumentParser()
        subparsers = parent.add_subparsers()
        auth_parser = create_auth_parser(subparsers)

        args = auth_parser.parse_args(["revoke-key", "KEY123", "--yes"])
        assert args.key_id == "KEY123"
        assert args.yes is True

    def test_assign_role_subcommand_defaults(self):
        from enterprise.auth.cli import create_auth_parser

        parent = argparse.ArgumentParser()
        subparsers = parent.add_subparsers()
        auth_parser = create_auth_parser(subparsers)

        args = auth_parser.parse_args(["assign-role", "--principal", "user:c", "--role", "admin"])
        assert args.scope == "global"

    def test_revoke_role_subcommand(self):
        from enterprise.auth.cli import create_auth_parser

        parent = argparse.ArgumentParser()
        subparsers = parent.add_subparsers()
        auth_parser = create_auth_parser(subparsers)

        args = auth_parser.parse_args(["revoke-role", "--principal", "user:c", "--role", "admin", "--scope", "tenant:x"])
        assert args.scope == "tenant:x"

    def test_create_key_with_roles_and_expires(self):
        from enterprise.auth.cli import create_auth_parser

        parent = argparse.ArgumentParser()
        subparsers = parent.add_subparsers()
        auth_parser = create_auth_parser(subparsers)

        args = auth_parser.parse_args([
            "create-key", "--principal", "user:a", "--name", "key1",
            "--role", "admin", "--role", "dev", "--expires", "30", "--tenant", "acme",
        ])
        assert args.role == ["admin", "dev"]
        assert args.expires == 30
        assert args.tenant == "acme"


class TestHandleAuthCommand:
    """Test handle_auth_command routing."""

    def test_routes_to_create_key(self, capsys):
        from enterprise.auth.cli import handle_auth_command
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(
            auth_command="create-key",
            principal="user:admin",
            name="Test Key",
            role=[],
            expires=None,
            tenant=None,
        )

        result = handle_auth_command(args, key_store, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "API Key created" in output

    def test_routes_to_list_keys_no_keys(self, capsys):
        from enterprise.auth.cli import handle_auth_command
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(auth_command="list-keys", principal="user:admin")
        result = handle_auth_command(args, key_store, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "No keys found" in output

    def test_routes_to_list_roles(self, capsys):
        from enterprise.auth.cli import handle_auth_command
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(auth_command="list-roles")
        result = handle_auth_command(args, key_store, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "Available built-in roles" in output

    def test_unknown_command_returns_1(self, capsys):
        from enterprise.auth.cli import handle_auth_command
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(auth_command="unknown-cmd")
        result = handle_auth_command(args, key_store, role_store)
        assert result == 1
        stderr_output = capsys.readouterr().err
        assert "Unknown auth command" in stderr_output


class TestHandleCreateKey:
    """Test _handle_create_key details."""

    def test_create_key_with_expiration(self, capsys):
        from enterprise.auth.cli import _handle_create_key
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(
            principal="user:admin",
            name="Expiring Key",
            role=[],
            expires=30,
            tenant=None,
        )
        result = _handle_create_key(args, key_store, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "Expires:" in output

    def test_create_key_with_invalid_role_fails(self, capsys):
        from enterprise.auth.cli import _handle_create_key
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(
            principal="user:admin",
            name="Key",
            role=["nonexistent_role"],
            expires=None,
            tenant=None,
        )
        result = _handle_create_key(args, key_store, role_store)
        assert result == 1
        stderr_output = capsys.readouterr().err
        assert "Unknown role" in stderr_output

    def test_create_key_with_valid_role_assigns_it(self, capsys):
        from enterprise.auth.cli import _handle_create_key
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(
            principal="user:admin",
            name="Key",
            role=["admin"],  # admin is a builtin role
            expires=None,
            tenant="acme",
        )
        result = _handle_create_key(args, key_store, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "Roles assigned: admin" in output

    def test_create_key_with_tenant(self, capsys):
        from enterprise.auth.cli import _handle_create_key
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        args = argparse.Namespace(
            principal="user:admin",
            name="Tenant Key",
            role=[],
            expires=None,
            tenant="acme",
        )
        result = _handle_create_key(args, key_store, role_store)
        assert result == 0


class TestHandleListKeys:
    """Test _handle_list_keys."""

    def test_list_keys_with_active_key(self, capsys):
        from enterprise.auth.cli import _handle_list_keys
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="My Key")

        args = argparse.Namespace(principal="user:admin")
        result = _handle_list_keys(args, key_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "ACTIVE" in output
        assert "My Key" in output

    def test_list_keys_with_revoked_key(self, capsys):
        from enterprise.auth.cli import _handle_list_keys
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="Revoked Key")
        keys = key_store.list_keys("user:admin")
        key_store.revoke_key(keys[0].key_id)

        args = argparse.Namespace(principal="user:admin")
        result = _handle_list_keys(args, key_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "REVOKED" in output


class TestHandleRevokeKey:
    """Test _handle_revoke_key."""

    def test_revoke_nonexistent_key(self, capsys):
        from enterprise.auth.cli import _handle_revoke_key
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        args = argparse.Namespace(key_id="nonexistent", yes=True)
        result = _handle_revoke_key(args, key_store)
        assert result == 1
        stderr_output = capsys.readouterr().err
        assert "not found" in stderr_output

    def test_revoke_already_revoked_key(self, capsys):
        from enterprise.auth.cli import _handle_revoke_key
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="Key")
        keys = key_store.list_keys("user:admin")
        key_id = keys[0].key_id
        key_store.revoke_key(key_id)

        args = argparse.Namespace(key_id=key_id, yes=True)
        result = _handle_revoke_key(args, key_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "already revoked" in output

    def test_revoke_with_confirmation_yes(self, capsys, monkeypatch):
        from enterprise.auth.cli import _handle_revoke_key
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="Key")
        keys = key_store.list_keys("user:admin")
        key_id = keys[0].key_id

        monkeypatch.setattr("builtins.input", lambda prompt: "y")
        args = argparse.Namespace(key_id=key_id, yes=False)
        result = _handle_revoke_key(args, key_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "revoked" in output

    def test_revoke_with_confirmation_cancelled(self, capsys, monkeypatch):
        from enterprise.auth.cli import _handle_revoke_key
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="Key")
        keys = key_store.list_keys("user:admin")
        key_id = keys[0].key_id

        monkeypatch.setattr("builtins.input", lambda prompt: "n")
        args = argparse.Namespace(key_id=key_id, yes=False)
        result = _handle_revoke_key(args, key_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "Cancelled" in output

    def test_revoke_with_yes_flag_skips_confirmation(self, capsys):
        from enterprise.auth.cli import _handle_revoke_key
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="Key")
        keys = key_store.list_keys("user:admin")
        key_id = keys[0].key_id

        args = argparse.Namespace(key_id=key_id, yes=True)
        result = _handle_revoke_key(args, key_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "revoked" in output.lower()

    def test_revoke_failure(self, capsys):
        from enterprise.auth.cli import _handle_revoke_key
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore

        key_store = InMemoryApiKeyStore()
        key_store.create_key(principal_id="user:admin", name="Key")
        keys = key_store.list_keys("user:admin")
        key_id = keys[0].key_id

        # Mock revoke_key to return False
        key_store.revoke_key = Mock(return_value=False)

        args = argparse.Namespace(key_id=key_id, yes=True)
        result = _handle_revoke_key(args, key_store)
        assert result == 1
        stderr_output = capsys.readouterr().err
        assert "Failed to revoke" in stderr_output


class TestHandleAssignRole:
    """Test _handle_assign_role."""

    def test_assign_unknown_role_fails(self, capsys):
        from enterprise.auth.cli import _handle_assign_role
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        role_store = InMemoryRoleStore()
        args = argparse.Namespace(principal="user:a", role="nonexistent", scope="global")
        result = _handle_assign_role(args, role_store)
        assert result == 1
        stderr_output = capsys.readouterr().err
        assert "Unknown role" in stderr_output

    def test_assign_valid_role_succeeds(self, capsys):
        from enterprise.auth.cli import _handle_assign_role
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        role_store = InMemoryRoleStore()
        args = argparse.Namespace(principal="user:a", role="admin", scope="global")
        result = _handle_assign_role(args, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "Assigned role" in output

    def test_assign_role_with_scope(self, capsys):
        from enterprise.auth.cli import _handle_assign_role
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        role_store = InMemoryRoleStore()
        args = argparse.Namespace(principal="user:a", role="developer", scope="tenant:acme")
        result = _handle_assign_role(args, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "tenant:acme" in output

    def test_assign_role_value_error_caught(self, capsys):
        from enterprise.auth.cli import _handle_assign_role
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        role_store = InMemoryRoleStore()
        role_store.assign_role = Mock(side_effect=ValueError("duplicate assignment"))
        # Need get_role to return something so we pass the unknown role check
        role_store.get_role = Mock(return_value=MagicMock())

        args = argparse.Namespace(principal="user:a", role="admin", scope="global")
        result = _handle_assign_role(args, role_store)
        assert result == 1
        stderr_output = capsys.readouterr().err
        assert "duplicate assignment" in stderr_output


class TestHandleRevokeRole:
    """Test _handle_revoke_role."""

    def test_revoke_role_succeeds(self, capsys):
        from enterprise.auth.cli import _handle_revoke_role
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        role_store = InMemoryRoleStore()
        args = argparse.Namespace(principal="user:a", role="admin", scope="global")
        result = _handle_revoke_role(args, role_store)
        assert result == 0
        output = capsys.readouterr().out
        assert "Revoked role" in output


class TestHandleListRoles:
    """Test _handle_list_roles."""

    def test_list_roles_output(self, capsys):
        from enterprise.auth.cli import _handle_list_roles

        result = _handle_list_roles()
        assert result == 0
        output = capsys.readouterr().out
        assert "Available built-in roles" in output
        # Should list at least admin role
        assert "admin" in output


# ===========================================================================
# Bootstrap tests
# ===========================================================================


class TestCreateStorageBackendsMemory:
    """Test _create_storage_backends with memory driver."""

    def test_memory_driver(self):
        from enterprise.auth.bootstrap import _create_storage_backends
        from enterprise.auth.config import AuthConfig, StorageConfig
        from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
        from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore

        config = AuthConfig(storage=StorageConfig(driver="memory"))
        api_key_store, role_store, tap_store = _create_storage_backends(config)

        assert isinstance(api_key_store, InMemoryApiKeyStore)
        assert isinstance(role_store, InMemoryRoleStore)
        assert tap_store is None


class TestCreateStorageBackendsEventSourcing:
    """Test _create_storage_backends with event_sourcing driver."""

    def test_event_sourcing_without_event_store_raises(self):
        from enterprise.auth.bootstrap import _create_storage_backends
        from enterprise.auth.config import AuthConfig, StorageConfig

        config = AuthConfig(storage=StorageConfig(driver="event_sourcing"))
        with pytest.raises(ValueError, match="requires event_store"):
            _create_storage_backends(config, event_store=None)

    def test_event_sourcing_with_event_store(self):
        from enterprise.auth.bootstrap import _create_storage_backends
        from enterprise.auth.config import AuthConfig, StorageConfig

        config = AuthConfig(storage=StorageConfig(driver="event_sourcing"))
        mock_event_store = MagicMock()
        mock_event_bus = MagicMock()

        api_key_store, role_store, tap_store = _create_storage_backends(
            config, event_store=mock_event_store, event_bus=mock_event_bus,
        )
        assert tap_store is None
        # Stores should be EventSourced instances
        assert api_key_store is not None
        assert role_store is not None


class TestCreateStorageBackendsSqlite:
    """Test _create_storage_backends with sqlite driver."""

    def test_sqlite_driver(self, tmp_path):
        from enterprise.auth.bootstrap import _create_storage_backends
        from enterprise.auth.config import AuthConfig, StorageConfig

        db_path = tmp_path / "auth" / "test.db"
        config = AuthConfig(storage=StorageConfig(driver="sqlite", path=str(db_path)))

        api_key_store, role_store, tap_store = _create_storage_backends(config)
        assert api_key_store is not None
        assert role_store is not None
        assert tap_store is not None


class TestCreateStorageBackendsPostgres:
    """Test _create_storage_backends with postgresql driver."""

    def test_postgres_driver(self):
        from enterprise.auth.bootstrap import _create_storage_backends
        from enterprise.auth.config import AuthConfig, StorageConfig

        config = AuthConfig(
            storage=StorageConfig(
                driver="postgresql",
                host="localhost",
                port=5432,
                database="testdb",
                user="testuser",
                password="testpass",
            )
        )

        # Patch the lazy imports for postgres stores
        with patch("enterprise.auth.infrastructure.postgres_store.create_postgres_connection_factory") as mock_factory, \
             patch("enterprise.auth.infrastructure.postgres_store.PostgresApiKeyStore") as mock_key_store_cls, \
             patch("enterprise.auth.infrastructure.postgres_store.PostgresRoleStore") as mock_role_store_cls:

            mock_factory.return_value = MagicMock()
            mock_key_instance = MagicMock()
            mock_role_instance = MagicMock()
            mock_key_store_cls.return_value = mock_key_instance
            mock_role_store_cls.return_value = mock_role_instance

            api_key_store, role_store, tap_store = _create_storage_backends(config)

            mock_factory.assert_called_once()
            mock_key_instance.initialize.assert_called_once()
            mock_role_instance.initialize.assert_called_once()
            assert tap_store is None

    def test_postgres_alias(self):
        from enterprise.auth.bootstrap import _create_storage_backends
        from enterprise.auth.config import AuthConfig, StorageConfig

        config = AuthConfig(storage=StorageConfig(driver="postgres"))

        with patch("enterprise.auth.infrastructure.postgres_store.create_postgres_connection_factory") as mock_factory, \
             patch("enterprise.auth.infrastructure.postgres_store.PostgresApiKeyStore") as mock_key_store_cls, \
             patch("enterprise.auth.infrastructure.postgres_store.PostgresRoleStore") as mock_role_store_cls:

            mock_factory.return_value = MagicMock()
            mock_key_store_cls.return_value = MagicMock()
            mock_role_store_cls.return_value = MagicMock()

            api_key_store, role_store, tap_store = _create_storage_backends(config)
            assert api_key_store is not None


class TestCreateStorageBackendsUnknown:
    """Test _create_storage_backends with unknown driver."""

    def test_unknown_driver_raises(self):
        from enterprise.auth.bootstrap import _create_storage_backends
        from enterprise.auth.config import AuthConfig, StorageConfig

        config = AuthConfig(storage=StorageConfig(driver="redis"))
        with pytest.raises(ValueError, match="Unknown auth storage driver"):
            _create_storage_backends(config)


class TestAuthComponents:
    """Test AuthComponents class."""

    def test_enabled_with_authenticators(self):
        from enterprise.auth.bootstrap import AuthComponents

        authn = MagicMock()
        authn._authenticators = [MagicMock()]
        authn._allow_anonymous = False
        authz = MagicMock()

        components = AuthComponents(authn_middleware=authn, authz_middleware=authz)
        assert components.enabled is True

    def test_enabled_with_no_authenticators_but_not_anonymous(self):
        from enterprise.auth.bootstrap import AuthComponents

        authn = MagicMock()
        authn._authenticators = []
        authn._allow_anonymous = False
        authz = MagicMock()

        components = AuthComponents(authn_middleware=authn, authz_middleware=authz)
        assert components.enabled is True

    def test_not_enabled_when_empty_authenticators_and_anonymous(self):
        from enterprise.auth.bootstrap import AuthComponents

        authn = MagicMock()
        authn._authenticators = []
        authn._allow_anonymous = True
        authz = MagicMock()

        components = AuthComponents(authn_middleware=authn, authz_middleware=authz)
        assert components.enabled is False

    def test_stores_accessible(self):
        from enterprise.auth.bootstrap import AuthComponents

        authn = MagicMock()
        authz = MagicMock()
        key_store = MagicMock()
        role_store = MagicMock()
        tap_store = MagicMock()

        components = AuthComponents(
            authn_middleware=authn,
            authz_middleware=authz,
            api_key_store=key_store,
            role_store=role_store,
            tap_store=tap_store,
        )
        assert components.api_key_store is key_store
        assert components.role_store is role_store
        assert components.tap_store is tap_store


class TestNullAuthComponents:
    """Test NullAuthComponents."""

    def test_enabled_returns_false(self):
        from enterprise.auth.bootstrap import NullAuthComponents

        null = NullAuthComponents()
        assert null.enabled is False

    def test_authn_returns_system_principal(self):
        from enterprise.auth.bootstrap import NullAuthComponents

        null = NullAuthComponents()
        # The NullAuthenticator inside should return system principal
        request = _make_auth_request({"Authorization": "Bearer fake"})
        principal = null.authn_middleware._authenticators[0].authenticate(request)
        assert principal.type.value == "system"

    def test_authz_allows_all(self):
        from enterprise.auth.bootstrap import NullAuthComponents

        null = NullAuthComponents()
        request = _make_authz_request()
        result = null.authz_middleware._authorizer.authorize(request)
        assert result.allowed
        assert "auth_disabled" in result.reason


class TestReplayTapPolicies:
    """Test _replay_tap_policies function."""

    def test_replay_provider_scope(self):
        from enterprise.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()
        mock_tap_store.list_all_policies.return_value = [
            ("provider", "my-provider", ["tool_a", "tool_b"], ["tool_c"]),
        ]

        mock_resolver = MagicMock()
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            _replay_tap_policies(mock_tap_store)
            mock_resolver.set_mcp_server_policy.assert_called_once()

    def test_replay_group_scope(self):
        from enterprise.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()
        mock_tap_store.list_all_policies.return_value = [
            ("group", "my-group", ["tool_x"], []),
        ]

        mock_resolver = MagicMock()
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            _replay_tap_policies(mock_tap_store)
            mock_resolver.set_group_policy.assert_called_once()

    def test_replay_member_scope_with_colon(self):
        from enterprise.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()
        mock_tap_store.list_all_policies.return_value = [
            ("member", "group1:member1", ["tool_y"], []),
        ]

        mock_resolver = MagicMock()
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            _replay_tap_policies(mock_tap_store)
            mock_resolver.set_member_policy.assert_called_once_with("group1", "member1", ANY)

    def test_replay_member_scope_without_colon(self):
        from enterprise.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()
        mock_tap_store.list_all_policies.return_value = [
            ("member", "standalone", ["tool_z"], []),
        ]

        mock_resolver = MagicMock()
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            _replay_tap_policies(mock_tap_store)
            mock_resolver.set_member_policy.assert_called_once_with("standalone", "standalone", ANY)

    def test_replay_exception_does_not_abort(self):
        from enterprise.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()
        mock_tap_store.list_all_policies.return_value = [
            ("provider", "p1", ["tool_a"], []),
            ("provider", "p2", ["tool_b"], []),
        ]

        mock_resolver = MagicMock()
        mock_resolver.set_mcp_server_policy.side_effect = [RuntimeError("fail"), None]

        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            # Should not raise -- fault barrier
            _replay_tap_policies(mock_tap_store)
            assert mock_resolver.set_mcp_server_policy.call_count == 2

    def test_resolver_none_skips(self):
        from enterprise.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()

        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=None,
        ):
            _replay_tap_policies(mock_tap_store)
            mock_tap_store.list_all_policies.assert_not_called()

    def test_import_error_fallback(self):
        from enterprise.auth.bootstrap import _replay_tap_policies

        mock_tap_store = MagicMock()
        mock_tap_store.list_all_policies.return_value = []

        mock_resolver = MagicMock()
        # Simulate get_tool_access_resolver raising ImportError at import time
        # by patching the module attribute to raise when called
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            side_effect=ImportError("no such function"),
        ):
            with patch.object(
                __import__("mcp_hangar.domain.services.tool_access_resolver", fromlist=["_resolver"]),
                "_resolver",
                mock_resolver,
                create=True,
            ):
                _replay_tap_policies(mock_tap_store)


class TestBootstrapAuth:
    """Test bootstrap_auth function."""

    def test_disabled_config_returns_null_components(self):
        from enterprise.auth.bootstrap import bootstrap_auth, NullAuthComponents
        from enterprise.auth.config import AuthConfig

        config = AuthConfig(enabled=False)
        result = bootstrap_auth(config)
        assert isinstance(result, NullAuthComponents)
        assert result.enabled is False

    def test_enabled_with_api_key_auth(self):
        from enterprise.auth.bootstrap import bootstrap_auth, AuthComponents
        from enterprise.auth.config import AuthConfig, StorageConfig, ApiKeyAuthConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=True, header_name="X-API-Key"),
        )
        result = bootstrap_auth(config)
        assert isinstance(result, AuthComponents)
        assert result.enabled is True
        assert len(result.authn_middleware._authenticators) >= 1

    def test_enabled_with_oidc_auth_incomplete_config_warns(self):
        from enterprise.auth.bootstrap import bootstrap_auth
        from enterprise.auth.config import AuthConfig, StorageConfig, OIDCAuthConfig, ApiKeyAuthConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            oidc=OIDCAuthConfig(enabled=True, issuer="", audience=""),  # incomplete
        )
        result = bootstrap_auth(config)
        # OIDC should NOT be added due to incomplete config
        # Only API key auth (disabled) and no OIDC -- 0 authenticators
        authenticator_count = len(result.authn_middleware._authenticators)
        assert authenticator_count == 0

    def test_enabled_with_oidc_complete_config(self):
        from enterprise.auth.bootstrap import bootstrap_auth
        from enterprise.auth.config import AuthConfig, StorageConfig, OIDCAuthConfig, ApiKeyAuthConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            oidc=OIDCAuthConfig(enabled=True, issuer="https://auth.example.com", audience="my-api"),
        )
        result = bootstrap_auth(config)
        assert len(result.authn_middleware._authenticators) == 1

    def test_role_assignments_from_config(self):
        from enterprise.auth.bootstrap import bootstrap_auth
        from enterprise.auth.config import AuthConfig, StorageConfig, RoleAssignment, ApiKeyAuthConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            role_assignments=[
                RoleAssignment(principal="user:admin", role="admin", scope="global"),
            ],
        )
        result = bootstrap_auth(config)
        # Should have assigned the admin role
        roles = result.role_store.get_roles_for_principal("user:admin")
        assert len(roles) >= 1

    def test_invalid_role_assignment_skipped(self):
        from enterprise.auth.bootstrap import bootstrap_auth
        from enterprise.auth.config import AuthConfig, StorageConfig, RoleAssignment, ApiKeyAuthConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            role_assignments=[
                RoleAssignment(principal="", role="admin"),  # invalid: empty principal
                RoleAssignment(principal="user:x", role=""),  # invalid: empty role
            ],
        )
        # Should not raise
        result = bootstrap_auth(config)
        assert result is not None

    def test_opa_enabled_wraps_with_combined_authorizer(self):
        from enterprise.auth.bootstrap import bootstrap_auth
        from enterprise.auth.config import AuthConfig, StorageConfig, OPAConfig, ApiKeyAuthConfig
        from enterprise.auth.infrastructure.opa_authorizer import CombinedAuthorizer

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            opa=OPAConfig(enabled=True, url="http://localhost:8181"),
        )
        result = bootstrap_auth(config)
        # The authorizer inside authz_middleware should be a CombinedAuthorizer
        assert isinstance(result.authz_middleware._authorizer, CombinedAuthorizer)

    def test_rate_limiter_disabled(self):
        from enterprise.auth.bootstrap import bootstrap_auth
        from enterprise.auth.config import AuthConfig, StorageConfig, RateLimitConfig, ApiKeyAuthConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            rate_limit=RateLimitConfig(enabled=False),
        )
        result = bootstrap_auth(config)
        # Rate limiter should not be passed to middleware
        assert result.authn_middleware._rate_limiter is None

    def test_rate_limiter_enabled(self):
        from enterprise.auth.bootstrap import bootstrap_auth
        from enterprise.auth.config import AuthConfig, StorageConfig, RateLimitConfig, ApiKeyAuthConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            rate_limit=RateLimitConfig(enabled=True, max_attempts=5, window_seconds=30),
        )
        result = bootstrap_auth(config)
        assert result.authn_middleware._rate_limiter is not None

    def test_tap_store_replay_called_when_present(self, tmp_path):
        from enterprise.auth.bootstrap import bootstrap_auth
        from enterprise.auth.config import AuthConfig, StorageConfig, ApiKeyAuthConfig

        db_path = tmp_path / "auth" / "test.db"
        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="sqlite", path=str(db_path)),
            api_key=ApiKeyAuthConfig(enabled=False),
        )

        with patch("enterprise.auth.bootstrap._replay_tap_policies") as mock_replay:
            result = bootstrap_auth(config)
            # tap_store should be non-None for sqlite driver
            assert result.tap_store is not None
            mock_replay.assert_called_once_with(result.tap_store)

    def test_role_assignment_value_error_logged(self):
        from enterprise.auth.bootstrap import bootstrap_auth
        from enterprise.auth.config import AuthConfig, StorageConfig, RoleAssignment, ApiKeyAuthConfig

        config = AuthConfig(
            enabled=True,
            storage=StorageConfig(driver="memory"),
            api_key=ApiKeyAuthConfig(enabled=False),
            role_assignments=[
                RoleAssignment(principal="user:x", role="admin"),
            ],
        )

        # Make assign_role raise ValueError
        with patch(
            "enterprise.auth.infrastructure.rbac_authorizer.InMemoryRoleStore.assign_role",
            side_effect=ValueError("test error"),
        ):
            # Should not raise -- logged and continued
            result = bootstrap_auth(config)
            assert result is not None
