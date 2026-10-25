"""An OIDC issuer can be bound to the tenants it may assert.

With two trusted issuers, nothing tied a token's tenant claim to the issuer
that signed it: issuer B could sign ``sub=alice, tenant_id=team-a`` and the
gateway built the same principal issuer A's alice gets. ``allowed_tenants`` on
an ``auth.oidc.issuers`` entry closes that, and boot warns when two or more
issuers are trusted and any of them has no binding.

Everything here is real: the config goes through ``parse_auth_config`` and
``bootstrap_auth``, tokens are RS256 signed with a key per issuer and verified
by the real ``JWKSTokenValidator`` and ``JWTAuthenticator``. Only the JWKS fetch
is replaced, by a stub that hands back the issuer's public key.
"""

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from structlog.testing import capture_logs

from mcp_hangar.auth.bootstrap import AuthComponents, bootstrap_auth
from mcp_hangar.auth.config import parse_auth_config
from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator
from mcp_hangar.domain.contracts.authentication import AuthRequest
from mcp_hangar.domain.exceptions import ConfigurationError, InvalidCredentialsError

pytestmark = pytest.mark.security

_ISSUER_A = "https://issuer-a.example.com"
_ISSUER_B = "https://issuer-b.example.com"
_AUDIENCE = "https://gateway.example.com"

_KEYS = {issuer: rsa.generate_private_key(public_exponent=65537, key_size=2048) for issuer in (_ISSUER_A, _ISSUER_B)}


class _SigningKey:
    """What ``PyJWKClient.get_signing_key_from_jwt`` returns."""

    def __init__(self, key: Any):
        self.key = key


class _StubJwksClient:
    """Stands in for the JWKS fetch only: answers with the issuer's public key."""

    def __init__(self, issuer: str):
        self._issuer = issuer

    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        return _SigningKey(_KEYS[self._issuer].public_key())


def _issuer_entry(issuer: str, **extra: Any) -> dict[str, Any]:
    return {"issuer": issuer, "audience": _AUDIENCE, "jwks_uri": f"{issuer}/jwks", **extra}


def _boot(issuers: list[dict[str, Any]], role_assignments: list[dict[str, str]] | None = None) -> AuthComponents:
    config = parse_auth_config(
        {
            "enabled": True,
            "api_key": {"enabled": False},
            "rate_limit": {"enabled": False},
            "oidc": {"enabled": True, "issuers": issuers},
            "role_assignments": role_assignments or [],
        }
    )
    components = bootstrap_auth(config)
    for authenticator in components.authn_middleware._authenticators:
        if isinstance(authenticator, JWTAuthenticator):
            for validator in authenticator._validator._validators.values():
                validator._jwks_client = _StubJwksClient(validator._config.issuer)
    return components


def _token(issuer: str, **claims: Any) -> str:
    now = int(time.time())
    payload = {"iss": issuer, "aud": _AUDIENCE, "iat": now, "exp": now + 300, **claims}
    return jwt.encode(payload, _KEYS[issuer], algorithm="RS256")


def _authenticate(components: AuthComponents, token: str):
    request = AuthRequest(headers={"authorization": f"Bearer {token}"}, source_ip="127.0.0.1")
    return components.authn_middleware.authenticate(request).principal


def _bound_pair() -> AuthComponents:
    return _boot(
        [
            _issuer_entry(_ISSUER_A, allowed_tenants=["team-a"]),
            _issuer_entry(_ISSUER_B, allowed_tenants=["team-b"]),
        ],
        role_assignments=[{"principal": "alice", "role": "viewer", "scope": "tenant:team-a"}],
    )


class TestABoundIssuer:
    def test_refuses_a_token_naming_another_issuers_tenant(self):
        components = _bound_pair()

        with pytest.raises(InvalidCredentialsError, match="Tenant not allowed for this issuer"):
            _authenticate(components, _token(_ISSUER_B, sub="alice", tenant_id="team-a"))

    def test_accepts_a_token_naming_its_own_tenant(self):
        components = _bound_pair()

        principal = _authenticate(components, _token(_ISSUER_B, sub="bob", tenant_id="team-b"))

        assert principal.id.value == "bob"
        assert principal.tenant_id == "team-b"

    def test_the_other_issuer_keeps_its_own_tenant(self):
        components = _bound_pair()

        principal = _authenticate(components, _token(_ISSUER_A, sub="alice", tenant_id="team-a"))

        assert principal.tenant_id == "team-a"
        assert components.authz_middleware.check(principal, "list", "tool", "*")

    def test_refuses_a_token_naming_no_tenant(self):
        # A token with no tenant would be a global principal and reach every
        # global role granted to its subject or groups.
        components = _bound_pair()

        with pytest.raises(InvalidCredentialsError, match="Tenant not allowed for this issuer"):
            _authenticate(components, _token(_ISSUER_B, sub="alice"))

    def test_refuses_a_tenant_claim_that_is_a_list(self):
        components = _bound_pair()

        with pytest.raises(InvalidCredentialsError, match="Tenant not allowed for this issuer"):
            _authenticate(components, _token(_ISSUER_B, sub="alice", tenant_id=["team-b"]))

    def test_a_same_subject_from_the_other_issuer_gets_none_of_the_tenant_scoped_role(self):
        # The subject is still the bare `sub`, so issuer B's alice is the same
        # principal id. What keeps her out of team-a's grant is that issuer B
        # can no longer name team-a, and a tenant-scoped grant needs the token's
        # tenant to match.
        components = _bound_pair()

        principal = _authenticate(components, _token(_ISSUER_B, sub="alice", tenant_id="team-b"))

        assert principal.id.value == "alice"
        assert not components.authz_middleware.check(principal, "list", "tool", "*")


class TestWithoutABinding:
    def test_a_single_issuer_still_accepts_any_tenant_and_does_not_warn(self):
        with capture_logs() as logs:
            components = _boot([_issuer_entry(_ISSUER_A)])

        principal = _authenticate(components, _token(_ISSUER_A, sub="alice", tenant_id="anything"))

        assert principal.tenant_id == "anything"
        assert not [entry for entry in logs if entry["event"] == "oidc_issuers_not_bound_to_tenants"]

    def test_two_unbound_issuers_behave_as_before_and_warn_once(self):
        with capture_logs() as logs:
            components = _boot([_issuer_entry(_ISSUER_A), _issuer_entry(_ISSUER_B)])

        principal = _authenticate(components, _token(_ISSUER_B, sub="alice", tenant_id="team-a"))

        assert principal.id.value == "alice"
        assert principal.tenant_id == "team-a"
        warnings = [entry for entry in logs if entry["event"] == "oidc_issuers_not_bound_to_tenants"]
        assert len(warnings) == 1
        assert warnings[0]["log_level"] == "warning"
        assert warnings[0]["issuers"] == [_ISSUER_A, _ISSUER_B]

    def test_the_warning_names_only_the_unbound_issuer(self):
        with capture_logs() as logs:
            _boot([_issuer_entry(_ISSUER_A, allowed_tenants=["team-a"]), _issuer_entry(_ISSUER_B)])

        warnings = [entry for entry in logs if entry["event"] == "oidc_issuers_not_bound_to_tenants"]
        assert [entry["issuers"] for entry in warnings] == [[_ISSUER_B]]

    def test_every_issuer_bound_does_not_warn(self):
        with capture_logs() as logs:
            _bound_pair()

        assert not [entry for entry in logs if entry["event"] == "oidc_issuers_not_bound_to_tenants"]


class TestAMalformedBindingRefusesTheConfig:
    @pytest.mark.parametrize("value", ["team-a", [], ["team-a", ""], ["team-a", 7], {"team-a": True}])
    def test_refused(self, value: Any):
        # Ignoring a binding that cannot be read would let the issuer name any
        # tenant, which is the opposite of what was asked for.
        with pytest.raises(ConfigurationError, match="allowed_tenants"):
            parse_auth_config({"oidc": {"enabled": True, "issuers": [_issuer_entry(_ISSUER_A, allowed_tenants=value)]}})

    def test_a_present_but_empty_key_refuses_the_config(self):
        # YAML `allowed_tenants:` with nothing after it is null; read as absent
        # it would leave the issuer unbound while the file looks bound.
        with pytest.raises(ConfigurationError, match="allowed_tenants"):
            parse_auth_config({"oidc": {"enabled": True, "issuers": [_issuer_entry(_ISSUER_A, allowed_tenants=None)]}})

    def test_an_absent_key_leaves_the_issuer_unbound(self):
        config = parse_auth_config({"oidc": {"enabled": True, "issuers": [_issuer_entry(_ISSUER_A)]}})

        assert config.oidc.issuers[0].allowed_tenants is None

    @pytest.mark.parametrize("issuers", [[], [_issuer_entry(_ISSUER_A)]])
    def test_a_top_level_list_refuses_the_config(self, issuers: list[dict[str, Any]]):
        # Copied to every issuer it would bind nothing; on the single-issuer
        # form it would be ignored. Either way it must not pass silently.
        with pytest.raises(ConfigurationError, match="allowed_tenants is per issuer"):
            parse_auth_config(
                {"oidc": {"enabled": True, "issuer": _ISSUER_A, "allowed_tenants": ["team-a"], "issuers": issuers}}
            )
