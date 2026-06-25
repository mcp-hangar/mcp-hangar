"""Tests for the OAuth multi-issuer trust registry.

Covers the acceptance criteria for trusting multiple OIDC authorization
servers concurrently:

- ``OIDCAuthConfig.resolved_issuers`` (multi / legacy single / empty).
- ``parse_auth_config`` parsing of ``oidc.issuers`` with per-issuer
  inheritance of the top-level ``oidc.*`` claim mappings.
- ``MultiIssuerTokenValidator`` fail-closed routing by the ``iss`` claim.
- ``JWTAuthenticator`` per-issuer claim mappings and lifetime enforcement.
- ``bootstrap_auth`` exposing the full ``oidc_issuers`` list.
- ``build_prm_response`` advertising every trusted issuer.

All example values use NEUTRAL placeholders (no real brand names). Network is
avoided everywhere: JWKS validators are constructed with ``jwks_uri`` set (lazy,
never fetched) and only routing / rejection paths that do NOT require signature
verification are exercised. Lightweight fakes stand in where a successful
"route and return claims" assertion is needed.
"""

import time

import jwt
import pytest

from mcp_hangar.auth.bootstrap import bootstrap_auth
from mcp_hangar.auth.config import (
    AuthConfig,
    OIDCAuthConfig,
    OIDCIssuerConfig,
    parse_auth_config,
)
from mcp_hangar.auth.infrastructure.jwt_authenticator import (
    JWKSTokenValidator,
    JWTAuthenticator,
    MultiIssuerTokenValidator,
    OIDCConfig,
)
from mcp_hangar.auth.prm import build_prm_response
from mcp_hangar.domain.contracts.authentication import AuthRequest
from mcp_hangar.domain.exceptions import (
    InvalidCredentialsError,
    TokenLifetimeExceededError,
)


# ---------------------------------------------------------------------------
# Neutral placeholders
# ---------------------------------------------------------------------------

_ISSUER_A = "https://issuer-a.example.com"
_ISSUER_B = "https://issuer-b.example.com"
_AUDIENCE_A = "mcp-hangar-a"
_AUDIENCE_B = "mcp-hangar-b"
_JWKS_A = "https://issuer-a.example.com/jwks"
_JWKS_B = "https://issuer-b.example.com/jwks"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FakeValidator:
    """Stand-in for a per-issuer JWKSTokenValidator.

    Exposes ``_config`` with an ``issuer`` attribute (the key
    MultiIssuerTokenValidator routes on) and a ``validate`` method that records
    the token it was called with and returns canned claims -- no signature
    verification, no network.
    """

    def __init__(self, issuer: str, claims: dict):
        self._config = OIDCConfig(issuer=issuer, audience="aud", jwks_uri="https://x/jwks")
        self._claims = claims
        self.validated_tokens: list[str] = []

    def validate(self, token: str) -> dict:
        self.validated_tokens.append(token)
        return self._claims


def _unsigned_token(claims: dict) -> str:
    """Encode an HS256 token whose ``iss`` is read unverified for routing.

    The MultiIssuerTokenValidator only decodes the unverified claims to select a
    validator, so a throwaway secret is fine here.
    """
    return jwt.encode(claims, "test-secret", algorithm="HS256")


def _auth_request(token: str) -> AuthRequest:
    return AuthRequest(headers={"Authorization": f"Bearer {token}"}, source_ip="127.0.0.1")


# ---------------------------------------------------------------------------
# resolved_issuers()
# ---------------------------------------------------------------------------


class TestResolvedIssuers:
    def test_explicit_multi_list_returned_as_is(self):
        entry_a = OIDCIssuerConfig(issuer=_ISSUER_A, audience=_AUDIENCE_A)
        entry_b = OIDCIssuerConfig(issuer=_ISSUER_B, audience=_AUDIENCE_B)
        cfg = OIDCAuthConfig(enabled=True, issuers=[entry_a, entry_b])

        resolved = cfg.resolved_issuers()

        assert resolved == [entry_a, entry_b]

    def test_legacy_single_issuer_synthesized_to_one_entry(self):
        cfg = OIDCAuthConfig(
            enabled=True,
            issuer=_ISSUER_A,
            audience=_AUDIENCE_A,
            groups_claim="teams",
            tenant_claim="org_id",
        )

        resolved = cfg.resolved_issuers()

        assert len(resolved) == 1
        only = resolved[0]
        assert only.issuer == _ISSUER_A
        assert only.audience == _AUDIENCE_A
        # Legacy top-level claim mappings carry into the synthesized entry.
        assert only.groups_claim == "teams"
        assert only.tenant_claim == "org_id"

    def test_explicit_issuers_take_precedence_over_legacy_single(self):
        entry = OIDCIssuerConfig(issuer=_ISSUER_B, audience=_AUDIENCE_B)
        cfg = OIDCAuthConfig(
            enabled=True,
            issuer=_ISSUER_A,
            audience=_AUDIENCE_A,
            issuers=[entry],
        )

        resolved = cfg.resolved_issuers()

        assert resolved == [entry]

    def test_empty_when_nothing_configured(self):
        cfg = OIDCAuthConfig(enabled=True)

        assert cfg.resolved_issuers() == []


# ---------------------------------------------------------------------------
# parse_auth_config: oidc.issuers
# ---------------------------------------------------------------------------


class TestParseAuthConfigIssuers:
    def test_issuers_list_parsed_into_oidc_issuer_configs(self):
        config_dict = {
            "enabled": True,
            "oidc": {
                "enabled": True,
                "issuers": [
                    {"issuer": _ISSUER_A, "audience": _AUDIENCE_A, "jwks_uri": _JWKS_A},
                    {"issuer": _ISSUER_B, "audience": _AUDIENCE_B, "jwks_uri": _JWKS_B},
                ],
            },
        }

        cfg = parse_auth_config(config_dict)

        assert len(cfg.oidc.issuers) == 2
        assert all(isinstance(e, OIDCIssuerConfig) for e in cfg.oidc.issuers)
        assert cfg.oidc.issuers[0].issuer == _ISSUER_A
        assert cfg.oidc.issuers[0].audience == _AUDIENCE_A
        assert cfg.oidc.issuers[0].jwks_uri == _JWKS_A
        assert cfg.oidc.issuers[1].issuer == _ISSUER_B
        assert cfg.oidc.issuers[1].jwks_uri == _JWKS_B

    def test_per_issuer_omitted_field_inherits_top_level(self):
        config_dict = {
            "enabled": True,
            "oidc": {
                "enabled": True,
                # Top-level mappings act as the per-issuer fallback.
                "groups_claim": "teams",
                "tenant_claim": "org_id",
                "issuers": [
                    # Omits groups_claim entirely -> inherits "teams".
                    {"issuer": _ISSUER_A, "audience": _AUDIENCE_A},
                    # Overrides groups_claim explicitly.
                    {"issuer": _ISSUER_B, "audience": _AUDIENCE_B, "groups_claim": "roles"},
                ],
            },
        }

        cfg = parse_auth_config(config_dict)

        inherited, overridden = cfg.oidc.issuers
        assert inherited.groups_claim == "teams"  # inherited from oidc.groups_claim
        assert inherited.tenant_claim == "org_id"  # inherited from oidc.tenant_claim
        assert overridden.groups_claim == "roles"  # explicit per-issuer value
        assert overridden.tenant_claim == "org_id"  # still inherited


# ---------------------------------------------------------------------------
# MultiIssuerTokenValidator: routing + fail-closed
# ---------------------------------------------------------------------------


class TestMultiIssuerRouting:
    def test_trusted_issuer_a_routes_to_a_validator(self):
        claims_a = {"iss": _ISSUER_A, "sub": "alice"}
        validator_a = _FakeValidator(_ISSUER_A, claims_a)
        validator_b = _FakeValidator(_ISSUER_B, {"iss": _ISSUER_B, "sub": "bob"})
        multi = MultiIssuerTokenValidator([validator_a, validator_b])

        token = _unsigned_token({"iss": _ISSUER_A, "sub": "alice"})
        result = multi.validate(token)

        assert result == claims_a
        assert validator_a.validated_tokens == [token]
        assert validator_b.validated_tokens == []

    def test_trusted_issuer_b_routes_to_b_validator(self):
        claims_b = {"iss": _ISSUER_B, "sub": "bob"}
        validator_a = _FakeValidator(_ISSUER_A, {"iss": _ISSUER_A, "sub": "alice"})
        validator_b = _FakeValidator(_ISSUER_B, claims_b)
        multi = MultiIssuerTokenValidator([validator_a, validator_b])

        token = _unsigned_token({"iss": _ISSUER_B, "sub": "bob"})
        result = multi.validate(token)

        assert result == claims_b
        assert validator_b.validated_tokens == [token]
        assert validator_a.validated_tokens == []

    def test_unknown_issuer_is_rejected_fail_closed(self):
        validator_a = _FakeValidator(_ISSUER_A, {"iss": _ISSUER_A, "sub": "alice"})
        multi = MultiIssuerTokenValidator([validator_a])

        token = _unsigned_token({"iss": "https://attacker.example.com", "sub": "mallory"})

        with pytest.raises(InvalidCredentialsError):
            multi.validate(token)
        # Untrusted token never reaches any wrapped validator.
        assert validator_a.validated_tokens == []

    def test_missing_iss_is_rejected(self):
        validator_a = _FakeValidator(_ISSUER_A, {"iss": _ISSUER_A, "sub": "alice"})
        multi = MultiIssuerTokenValidator([validator_a])

        token = _unsigned_token({"sub": "alice"})  # no iss claim

        with pytest.raises(InvalidCredentialsError):
            multi.validate(token)
        assert validator_a.validated_tokens == []

    def test_garbage_token_is_rejected(self):
        validator_a = _FakeValidator(_ISSUER_A, {"iss": _ISSUER_A, "sub": "alice"})
        multi = MultiIssuerTokenValidator([validator_a])

        with pytest.raises(InvalidCredentialsError):
            multi.validate("not-a-jwt")
        assert validator_a.validated_tokens == []

    def test_jwks_validators_constructed_without_network(self):
        """Constructing real JWKS validators with jwks_uri set must not fetch."""
        validator_a = JWKSTokenValidator(OIDCConfig(issuer=_ISSUER_A, audience=_AUDIENCE_A, jwks_uri=_JWKS_A))
        validator_b = JWKSTokenValidator(OIDCConfig(issuer=_ISSUER_B, audience=_AUDIENCE_B, jwks_uri=_JWKS_B))
        multi = MultiIssuerTokenValidator([validator_a, validator_b])

        # Untrusted iss is rejected before any JWKS client is created (lazy init).
        token = _unsigned_token({"iss": "https://untrusted.example.com", "sub": "x"})
        with pytest.raises(InvalidCredentialsError):
            multi.validate(token)
        assert validator_a._jwks_client is None
        assert validator_b._jwks_client is None


# ---------------------------------------------------------------------------
# JWTAuthenticator: per-issuer claim mappings + lifetime
# ---------------------------------------------------------------------------


class TestJWTAuthenticatorPerIssuer:
    def _build_authenticator(self):
        config_a = OIDCConfig(
            issuer=_ISSUER_A,
            audience=_AUDIENCE_A,
            jwks_uri=_JWKS_A,
            groups_claim="groups",
            tenant_claim="tenant_id",
            max_token_lifetime=86400,  # generous: accepts long tokens
        )
        config_b = OIDCConfig(
            issuer=_ISSUER_B,
            audience=_AUDIENCE_B,
            jwks_uri=_JWKS_B,
            groups_claim="roles",
            tenant_claim="org_id",
            max_token_lifetime=300,  # short: rejects long tokens
        )
        # The validator is irrelevant here -- a fake returns canned claims so we
        # isolate the authenticator's per-issuer claim/lifetime selection.
        issuer_configs = {_ISSUER_A: config_a, _ISSUER_B: config_b}
        return config_a, config_b, issuer_configs

    def test_issuer_a_uses_groups_claim(self):
        config_a, _config_b, issuer_configs = self._build_authenticator()
        now = int(time.time())
        claims_a = {
            "iss": _ISSUER_A,
            "sub": "alice",
            "groups": ["team-x"],
            "tenant_id": "tenant-1",
            "iat": now,
            "exp": now + 3600,
        }
        validator = _FakeValidator(_ISSUER_A, claims_a)
        authn = JWTAuthenticator(config_a, validator, issuer_configs=issuer_configs)

        principal = authn.authenticate(_auth_request(_unsigned_token(claims_a)))

        assert principal.id.value == "alice"
        assert "team-x" in principal.groups
        assert principal.tenant_id == "tenant-1"

    def test_issuer_b_uses_roles_claim(self):
        config_a, _config_b, issuer_configs = self._build_authenticator()
        now = int(time.time())
        claims_b = {
            "iss": _ISSUER_B,
            "sub": "bob",
            "roles": ["admin"],  # B maps groups from "roles"
            "org_id": "tenant-2",  # B maps tenant from "org_id"
            "iat": now,
            "exp": now + 60,
        }
        validator = _FakeValidator(_ISSUER_B, claims_b)
        authn = JWTAuthenticator(config_a, validator, issuer_configs=issuer_configs)

        principal = authn.authenticate(_auth_request(_unsigned_token(claims_b)))

        assert principal.id.value == "bob"
        assert "admin" in principal.groups
        assert principal.tenant_id == "tenant-2"

    def test_issuer_b_rejects_overlong_token_lifetime(self):
        config_a, _config_b, issuer_configs = self._build_authenticator()
        now = int(time.time())
        # 1 hour lifetime exceeds B's 300s max.
        claims_b = {
            "iss": _ISSUER_B,
            "sub": "bob",
            "iat": now,
            "exp": now + 3600,
        }
        validator = _FakeValidator(_ISSUER_B, claims_b)
        authn = JWTAuthenticator(config_a, validator, issuer_configs=issuer_configs)

        with pytest.raises(TokenLifetimeExceededError):
            authn.authenticate(_auth_request(_unsigned_token(claims_b)))

    def test_issuer_a_accepts_same_overlong_token_lifetime(self):
        config_a, _config_b, issuer_configs = self._build_authenticator()
        now = int(time.time())
        # Same 1 hour lifetime is within A's 86400s max.
        claims_a = {
            "iss": _ISSUER_A,
            "sub": "alice",
            "iat": now,
            "exp": now + 3600,
        }
        validator = _FakeValidator(_ISSUER_A, claims_a)
        authn = JWTAuthenticator(config_a, validator, issuer_configs=issuer_configs)

        principal = authn.authenticate(_auth_request(_unsigned_token(claims_a)))

        assert principal.id.value == "alice"

    def test_unknown_iss_falls_back_to_default_config(self):
        config_a, _config_b, issuer_configs = self._build_authenticator()
        now = int(time.time())
        # iss not in issuer_configs -> default (config_a) mappings apply.
        claims = {
            "iss": "https://other.example.com",
            "sub": "carol",
            "groups": ["default-team"],  # config_a maps groups from "groups"
            "tenant_id": "tenant-d",
            "iat": now,
            "exp": now + 3600,
        }
        validator = _FakeValidator(_ISSUER_A, claims)
        authn = JWTAuthenticator(config_a, validator, issuer_configs=issuer_configs)

        principal = authn.authenticate(_auth_request(_unsigned_token(claims)))

        assert principal.id.value == "carol"
        assert "default-team" in principal.groups
        assert principal.tenant_id == "tenant-d"


# ---------------------------------------------------------------------------
# bootstrap_auth: oidc_issuers list
# ---------------------------------------------------------------------------


class TestBootstrapAuthIssuers:
    def test_two_issuer_config_yields_two_oidc_issuers(self):
        config = AuthConfig(
            enabled=True,
            oidc=OIDCAuthConfig(
                enabled=True,
                issuers=[
                    OIDCIssuerConfig(issuer=_ISSUER_A, audience=_AUDIENCE_A, jwks_uri=_JWKS_A),
                    OIDCIssuerConfig(issuer=_ISSUER_B, audience=_AUDIENCE_B, jwks_uri=_JWKS_B),
                ],
            ),
        )

        components = bootstrap_auth(config)

        assert len(components.oidc_issuers) == 2
        assert components.oidc_issuers == [_ISSUER_A, _ISSUER_B]
        # Compat single field holds the first issuer.
        assert components.oidc_issuer == _ISSUER_A

    def test_legacy_single_issuer_yields_one_oidc_issuer(self):
        config = AuthConfig(
            enabled=True,
            oidc=OIDCAuthConfig(
                enabled=True,
                issuer=_ISSUER_A,
                audience=_AUDIENCE_A,
                jwks_uri=_JWKS_A,
            ),
        )

        components = bootstrap_auth(config)

        assert len(components.oidc_issuers) == 1
        assert components.oidc_issuers == [_ISSUER_A]
        assert components.oidc_issuer == _ISSUER_A


# ---------------------------------------------------------------------------
# PRM: advertises every trusted issuer
# ---------------------------------------------------------------------------


class TestPrmMultiIssuer:
    def test_authorization_servers_lists_all_issuers(self):
        body = build_prm_response(issuers=[_ISSUER_A, _ISSUER_B], resource_uri="https://mcp.example.com")

        assert body["resource"] == "https://mcp.example.com"
        assert body["authorization_servers"] == [_ISSUER_A, _ISSUER_B]
