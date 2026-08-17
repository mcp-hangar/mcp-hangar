"""Hardening for multi-issuer routing: non-string `iss` and duplicate issuers.

Regression coverage for the post-merge review of #273 (#277).
"""

import base64
import json

from mcp_hangar.auth.bootstrap import bootstrap_auth
from mcp_hangar.auth.config import AuthConfig, OIDCAuthConfig, OIDCIssuerConfig
from mcp_hangar.auth.infrastructure.jwt_authenticator import (
    JWKSTokenValidator,
    MultiIssuerTokenValidator,
    OIDCConfig,
)
from mcp_hangar.domain.exceptions import InvalidCredentialsError


def _craft(iss) -> str:
    """Hand-craft an (unsigned) JWT whose `iss` claim is an arbitrary JSON value.

    PyJWT's encode() refuses a non-string iss, so we build the token by hand to
    exercise the unverified-decode routing path.
    """

    def seg(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': 'HS256', 'typ': 'JWT'})}.{seg({'iss': iss, 'aud': 'h'})}.sig"


def _validator() -> MultiIssuerTokenValidator:
    return MultiIssuerTokenValidator(
        [JWKSTokenValidator(OIDCConfig(issuer="https://idp-a/", audience="h", jwks_uri="https://idp-a/jwks"))]
    )


class TestNonStringIssuer:
    def test_iss_as_list_is_rejected_not_500(self):
        try:
            _validator().validate(_craft(["https://idp-a/", "https://evil/"]))
            raise AssertionError("list iss was not rejected")
        except InvalidCredentialsError:
            pass  # clean 401, not an uncaught TypeError

    def test_iss_as_object_is_rejected_not_500(self):
        try:
            _validator().validate(_craft({"x": 1}))
            raise AssertionError("object iss was not rejected")
        except InvalidCredentialsError:
            pass

    def test_iss_as_number_is_rejected(self):
        try:
            _validator().validate(_craft(123))
            raise AssertionError("numeric iss was not rejected")
        except InvalidCredentialsError:
            pass


class TestDuplicateIssuers:
    def test_duplicate_issuers_do_not_crash_bootstrap_and_dedupe(self):
        cfg = AuthConfig(
            enabled=True,
            oidc=OIDCAuthConfig(
                enabled=True,
                issuers=[
                    OIDCIssuerConfig(issuer="https://dup/", audience="h", jwks_uri="https://dup/jwks"),
                    OIDCIssuerConfig(issuer="https://dup/", audience="h2", jwks_uri="https://dup/jwks2"),
                ],
            ),
        )
        ac = bootstrap_auth(cfg)
        jwt_auth = next(a for a in ac.authn_middleware._authenticators if hasattr(a, "_issuer_configs"))
        # The duplicate issuer key resolves to a single (last-wins) config -- no crash.
        assert list(jwt_auth._issuer_configs.keys()) == ["https://dup/"]


class TestAlgNone:
    """Regression for #992: alg=none answered 500, plain garbage answered 401.

    Issuer routing does an unverified decode, so an alg=none token with a
    configured `iss` reaches the real validator; the crash was
    PyJWKClientError escaping validate() (it is not an InvalidTokenError
    subclass). The stub raises exactly what PyJWKClient raises for a token
    with no usable signing key, without needing a network JWKS fetch.
    """

    def test_alg_none_is_rejected_not_500(self):
        import jwt as pyjwt

        def seg(d: dict) -> str:
            return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

        token = f"{seg({'alg': 'none', 'typ': 'JWT'})}.{seg({'iss': 'https://idp-a/', 'aud': 'h'})}."

        inner = JWKSTokenValidator(OIDCConfig(issuer="https://idp-a/", audience="h", jwks_uri="https://idp-a/jwks"))

        class _StubJWKSClient:
            def get_signing_key_from_jwt(self, _token):
                raise pyjwt.exceptions.PyJWKClientError('Unable to find a signing key that matches: ""')

        inner._jwks_client = _StubJWKSClient()

        validator = MultiIssuerTokenValidator([inner])
        try:
            validator.validate(token)
            raise AssertionError("alg=none token was not rejected")
        except InvalidCredentialsError:
            pass  # clean 401, not PyJWKClientError escaping as a 500
