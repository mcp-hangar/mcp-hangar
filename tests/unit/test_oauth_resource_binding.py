"""RFC 8707 resource-indicator audience binding.

When ``auth.oidc.resource_uri`` is configured, every accepted token's ``aud``
must match it (the same value advertised as PRM ``resource``), overriding any
per-issuer ``audience``. Without a configured resource URI, validation falls
back to the per-issuer audience.
"""

from unittest.mock import MagicMock, patch

import jwt as real_jwt

from mcp_hangar.auth.bootstrap import bootstrap_auth
from mcp_hangar.auth.config import AuthConfig, OIDCAuthConfig, OIDCIssuerConfig
from mcp_hangar.auth.infrastructure.jwt_authenticator import JWKSTokenValidator, OIDCConfig
from mcp_hangar.auth.prm import build_prm_response
from mcp_hangar.domain.exceptions import InvalidCredentialsError

RESOURCE = "https://hangar.example.com"


def _jwt_authenticator(components):
    """Return the JWTAuthenticator (the one carrying per-issuer configs)."""
    return next(a for a in components.authn_middleware._authenticators if hasattr(a, "_issuer_configs"))


class TestBootstrapResourceBinding:
    def test_resource_uri_overrides_per_issuer_audience(self):
        cfg = AuthConfig(
            enabled=True,
            oidc=OIDCAuthConfig(
                enabled=True,
                resource_uri=RESOURCE,
                issuers=[
                    OIDCIssuerConfig(issuer="https://idp-a/", audience="legacy-a", jwks_uri="https://idp-a/jwks"),
                    OIDCIssuerConfig(issuer="https://idp-b/", audience="legacy-b", jwks_uri="https://idp-b/jwks"),
                ],
            ),
        )
        ac = bootstrap_auth(cfg)
        configs = _jwt_authenticator(ac)._issuer_configs
        # Every issuer's validation audience is bound to the resource URI.
        assert {c.audience for c in configs.values()} == {RESOURCE}

    def test_without_resource_uri_falls_back_to_per_issuer_audience(self):
        cfg = AuthConfig(
            enabled=True,
            oidc=OIDCAuthConfig(
                enabled=True,
                issuers=[
                    OIDCIssuerConfig(issuer="https://idp-a/", audience="aud-a", jwks_uri="https://idp-a/jwks"),
                    OIDCIssuerConfig(issuer="https://idp-b/", audience="aud-b", jwks_uri="https://idp-b/jwks"),
                ],
            ),
        )
        ac = bootstrap_auth(cfg)
        configs = _jwt_authenticator(ac)._issuer_configs
        assert configs["https://idp-a/"].audience == "aud-a"
        assert configs["https://idp-b/"].audience == "aud-b"

    def test_legacy_single_issuer_binds_to_resource_uri(self):
        cfg = AuthConfig(
            enabled=True,
            oidc=OIDCAuthConfig(
                enabled=True,
                issuer="https://solo/",
                audience="legacy-aud",
                jwks_uri="https://solo/jwks",
                resource_uri=RESOURCE,
            ),
        )
        ac = bootstrap_auth(cfg)
        configs = _jwt_authenticator(ac)._issuer_configs
        assert configs["https://solo/"].audience == RESOURCE


class TestValidatorAudienceIsResource:
    def test_validate_passes_resource_uri_as_audience(self):
        config = OIDCConfig(issuer="https://idp/", audience=RESOURCE, jwks_uri="https://idp/jwks")
        validator = JWKSTokenValidator(config)

        mock_client = MagicMock()
        mock_key = MagicMock()
        mock_key.key = "fake_key"
        mock_client.get_signing_key_from_jwt.return_value = mock_key
        validator._jwks_client = mock_client

        with patch("jwt.decode", return_value={"sub": "u", "iss": "https://idp/", "aud": RESOURCE}) as mock_decode:
            validator.validate("some.token.here")

        # The expected audience handed to PyJWT is the resource URI.
        assert mock_decode.call_args.kwargs["audience"] == RESOURCE

    def test_token_for_other_resource_is_rejected(self):
        config = OIDCConfig(issuer="https://idp/", audience=RESOURCE, jwks_uri="https://idp/jwks")
        validator = JWKSTokenValidator(config)

        mock_client = MagicMock()
        mock_key = MagicMock()
        mock_key.key = "fake_key"
        mock_client.get_signing_key_from_jwt.return_value = mock_key
        validator._jwks_client = mock_client

        with patch("jwt.decode", side_effect=real_jwt.InvalidAudienceError("bad aud")):
            try:
                validator.validate("some.token.here")
                raise AssertionError("token for another resource was accepted")
            except InvalidCredentialsError as exc:
                assert "audience" in str(exc)


class TestAdvertiseAndValidateAgree:
    def test_prm_resource_equals_validation_audience(self):
        cfg = AuthConfig(
            enabled=True,
            oidc=OIDCAuthConfig(
                enabled=True,
                resource_uri=RESOURCE,
                issuers=[OIDCIssuerConfig(issuer="https://idp/", jwks_uri="https://idp/jwks")],
            ),
        )
        ac = bootstrap_auth(cfg)
        validation_audience = next(iter(_jwt_authenticator(ac)._issuer_configs.values())).audience
        prm = build_prm_response(issuers=ac.oidc_issuers, resource_uri=ac.oidc_resource_uri)
        # What we advertise as the resource is exactly what we validate aud against.
        assert prm["resource"] == RESOURCE == validation_audience
