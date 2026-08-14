"""`JWTAuthenticator` and its token validators: what a token must carry to authenticate."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar.domain.contracts.authentication import AuthRequest, ITokenValidator
from mcp_hangar.domain.exceptions import (
    ExpiredCredentialsError,
    InvalidCredentialsError,
    TokenLifetimeExceededError,
)
from mcp_hangar.domain.value_objects import PrincipalType


def _make_auth_request(headers: dict[str, str] | None = None, source_ip: str = "127.0.0.1") -> AuthRequest:
    return AuthRequest(
        headers=headers or {},
        source_ip=source_ip,
    )


class TestOIDCConfig:
    """Test OIDCConfig dataclass defaults and field mappings."""

    def test_defaults(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import OIDCConfig

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
        from mcp_hangar.auth.infrastructure.jwt_authenticator import OIDCConfig

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
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Bearer eyJ..."})
        assert authn.supports(request) is True

    def test_supports_lowercase_authorization_header(self):
        # HTTP transports normalise header keys to lowercase; a real Bearer token
        # must still be recognised (regression for #311). See jwt_authenticator.supports.
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"authorization": "Bearer eyJ..."})
        assert authn.supports(request) is True

    def test_does_not_support_basic_auth(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Basic dXNlcjpwYXNz"})
        assert authn.supports(request) is False

    def test_does_not_support_missing_header(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({})
        assert authn.supports(request) is False


class TestJWTAuthenticatorAuthenticate:
    """Test JWTAuthenticator.authenticate method."""

    def test_missing_bearer_raises(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Basic dXNlcjpwYXNz"})
        with pytest.raises(InvalidCredentialsError, match="Missing Bearer token"):
            authn.authenticate(request)

    def test_empty_bearer_token_raises(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Bearer "})
        with pytest.raises(InvalidCredentialsError, match="Empty Bearer token"):
            authn.authenticate(request)

    def test_successful_authenticate(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

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
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=0)
        validator = Mock(spec=ITokenValidator)
        validator.validate.return_value = {"groups": [], "iss": "https://x"}
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Bearer valid_token"})
        with pytest.raises(InvalidCredentialsError, match="Missing sub claim"):
            authn.authenticate(request)

    def test_groups_as_string_converted_to_list(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=0)
        validator = Mock(spec=ITokenValidator)
        validator.validate.return_value = {"sub": "user1", "groups": "single_group"}
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Bearer tok"})
        principal = authn.authenticate(request)
        assert "single_group" in principal.groups

    def test_empty_groups_default(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=0)
        validator = Mock(spec=ITokenValidator)
        validator.validate.return_value = {"sub": "user1"}
        authn = JWTAuthenticator(config, validator)

        request = _make_auth_request({"Authorization": "Bearer tok"})
        principal = authn.authenticate(request)
        assert principal.groups == frozenset()

    def test_lifetime_enforcement_called(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

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
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=0)
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        # Should not raise even without iat/exp claims
        authn._enforce_token_lifetime({})

    def test_disabled_when_max_lifetime_negative(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=-1)
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)
        authn._enforce_token_lifetime({})

    def test_missing_iat_raises(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=3600)
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        with pytest.raises(InvalidCredentialsError, match="iat"):
            authn._enforce_token_lifetime({"exp": 9999})

    def test_missing_exp_raises(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y", max_token_lifetime=3600)
        validator = Mock(spec=ITokenValidator)
        authn = JWTAuthenticator(config, validator)

        with pytest.raises(InvalidCredentialsError, match="exp"):
            authn._enforce_token_lifetime({"iat": 1000})


class TestJWKSTokenValidator:
    """Test JWKSTokenValidator."""

    def test_pyjwt_not_installed_raises(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

        config = OIDCConfig(issuer="https://x", audience="y")
        validator = JWKSTokenValidator(config)

        with patch.dict("sys.modules", {"jwt": None}):
            with pytest.raises(InvalidCredentialsError, match="PyJWT"):
                validator.validate("some.token.here")

    def test_expired_signature_raises_expired_credentials(self):
        import jwt as real_jwt

        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

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
        import jwt as real_jwt

        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

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
        import jwt as real_jwt

        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

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
        import jwt as real_jwt

        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

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
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

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
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

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
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

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
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

        config = OIDCConfig(issuer="https://auth.example.com", audience="y")
        validator = JWKSTokenValidator(config)

        mock_response = MagicMock()
        mock_response.json.return_value = {}  # no jwks_uri
        mock_response.raise_for_status.return_value = None

        with patch("httpx.get", return_value=mock_response):
            with pytest.raises(InvalidCredentialsError, match="did not return jwks_uri"):
                validator._init_jwks_client()

    def test_oidc_discovery_http_error_raises(self):
        import httpx

        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

        config = OIDCConfig(issuer="https://auth.example.com", audience="y")
        validator = JWKSTokenValidator(config)

        with patch("httpx.get", side_effect=httpx.HTTPError("connection refused")):
            with pytest.raises(InvalidCredentialsError, match="discover OIDC"):
                validator._init_jwks_client()

    def test_non_https_issuer_logs_warning(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

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
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

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
        from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig

        config = OIDCConfig(issuer="https://auth.example.com", audience="y")
        validator = JWKSTokenValidator(config)

        with patch.dict("sys.modules", {"httpx": None}):
            with pytest.raises(InvalidCredentialsError, match="additional libraries"):
                validator._init_jwks_client()


class TestStaticSecretTokenValidator:
    """Test StaticSecretTokenValidator."""

    def test_valid_token_with_hs256(self):
        import jwt

        from mcp_hangar.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator

        secret = "test-secret-key"
        validator = StaticSecretTokenValidator(secret)
        now = int(time.time())
        token = jwt.encode({"sub": "user1", "iat": now, "exp": now + 3600}, secret, algorithm="HS256")

        claims = validator.validate(token)
        assert claims["sub"] == "user1"

    def test_expired_token_raises(self):
        import jwt

        from mcp_hangar.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator

        secret = "test-secret-key"
        validator = StaticSecretTokenValidator(secret)
        now = int(time.time())
        token = jwt.encode({"sub": "user1", "iat": now - 7200, "exp": now - 3600}, secret, algorithm="HS256")

        with pytest.raises(ExpiredCredentialsError, match="expired"):
            validator.validate(token)

    def test_invalid_token_raises(self):
        from mcp_hangar.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator

        validator = StaticSecretTokenValidator("correct-secret")
        import jwt

        token = jwt.encode({"sub": "user1"}, "wrong-secret", algorithm="HS256")

        with pytest.raises(InvalidCredentialsError, match="Invalid JWT"):
            validator.validate(token)

    def test_with_issuer_and_audience(self):
        import jwt

        from mcp_hangar.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator

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
        import jwt

        from mcp_hangar.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator

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
        import jwt

        from mcp_hangar.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator

        secret = "test-secret-key"
        # No issuer or audience set -> should not verify them
        validator = StaticSecretTokenValidator(secret)
        now = int(time.time())
        token = jwt.encode({"sub": "user1", "iat": now, "exp": now + 3600}, secret, algorithm="HS256")
        claims = validator.validate(token)
        assert claims["sub"] == "user1"
