"""JWT validation tolerates clock drift against the issuer (#630).

PyJWT defaults ``leeway`` to 0, so ``exp``/``iat``/``nbf`` had to agree with this
host to the second. Clocks routinely do not: a VM resuming from a snapshot, a
container host whose NTP has drifted, an IdP a few seconds ahead. Skew is a
property of the *pair* of hosts, so the failure is total rather than partial --
every token is rejected at once, with valid credentials and a healthy IdP. It
reads as "authentication broke everywhere" and there is nothing in the token to
explain it.

The tests below pin both halves, because a leeway that swallows genuinely expired
tokens would be a worse bug than the one being fixed:

* a token from a clock a little ahead, or one that expired a moment ago, is
  accepted within the tolerance;
* a token expired well beyond the tolerance is still rejected.

Written against ``StaticSecretTokenValidator`` (HS256) because it needs no JWKS
endpoint. The JWKS path takes the same value from
``OIDCConfig.clock_skew_leeway`` and passes it to the same ``jwt.decode``
parameter.
"""

from __future__ import annotations

import datetime as dt

import jwt
import pytest

from mcp_hangar.auth.infrastructure.jwt_authenticator import StaticSecretTokenValidator
from mcp_hangar.domain.exceptions import ExpiredCredentialsError, InvalidCredentialsError

SECRET = "test-secret-not-a-real-key-padded-to-32-bytes-minimum"
LEEWAY = 60


def _token(*, issued_offset: int = 0, expires_offset: int = 3600) -> str:
    """Mint an HS256 token whose time claims are shifted by *issued_offset*.

    A positive offset simulates an issuer whose clock runs ahead of ours.
    """
    now = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=issued_offset)
    return jwt.encode(
        {
            "sub": "alice",
            "iat": now,
            "nbf": now,
            "exp": now + dt.timedelta(seconds=expires_offset),
        },
        SECRET,
        algorithm="HS256",
    )


def _validator(leeway: int = LEEWAY) -> StaticSecretTokenValidator:
    return StaticSecretTokenValidator(SECRET, clock_skew_leeway=leeway)


class TestDriftIsTolerated:
    def test_issuer_clock_ahead_within_leeway_is_accepted(self) -> None:
        """The common case: the IdP is a few seconds ahead of this host.

        Without leeway both ``iat`` and ``nbf`` sit in our future and PyJWT
        rejects the token as not-yet-valid -- for every token it issues.
        """
        claims = _validator().validate(_token(issued_offset=30))

        assert claims["sub"] == "alice"

    def test_token_expired_within_leeway_is_accepted(self) -> None:
        """Expired 30s ago, tolerance 60s: still accepted."""
        claims = _validator().validate(_token(issued_offset=-3630, expires_offset=3600))

        assert claims["sub"] == "alice"


class TestExpiryIsStillEnforced:
    def test_token_expired_beyond_leeway_is_rejected(self) -> None:
        """The half that matters: leeway must not become an expiry extension."""
        with pytest.raises(ExpiredCredentialsError):
            _validator().validate(_token(issued_offset=-7200, expires_offset=3600))

    def test_zero_leeway_restores_exact_agreement(self) -> None:
        """Opt back into the old behaviour for deployments that want it."""
        with pytest.raises((ExpiredCredentialsError, InvalidCredentialsError)):
            _validator(leeway=0).validate(_token(issued_offset=30))


class TestDefault:
    def test_leeway_defaults_to_sixty_seconds(self) -> None:
        """Deployments that configure nothing get the tolerance, not 0.

        The bug was the *default*, so this is the assertion that would have
        caught it.
        """
        assert StaticSecretTokenValidator(SECRET)._clock_skew_leeway == 60

    def test_oidc_config_default_matches(self) -> None:
        from mcp_hangar.auth.infrastructure.jwt_authenticator import OIDCConfig

        config = OIDCConfig(issuer="https://idp.example", audience="hangar")

        assert config.clock_skew_leeway == 60
