"""Fail-closed per-tenant token isolation (issue #312, variant A).

These tests pin the hardening of the claim-based multi-tenant model: the
effective tenant of a request must derive SOLELY from the validated token's
tenant claim, and a token that names no tenant must not transit as a
global/any-tenant principal.

Scope proven here (no network, deterministic -- a stub validator returns canned
claims, so signature/JWKS verification is never exercised):

- Multi-tenant mode (``require_tenant=True``) + token with NO / empty tenant
  claim -> rejected (fail-closed).
- Token with tenant claim = A -> effective tenant is A. A client attempt to
  override the tenant to B via request headers / metadata does NOT change the
  effective tenant (there is no header/param path that feeds the effective
  tenant; it comes from the claim only).
- Single-tenant / no-OIDC mode (``require_tenant=False``, the default) still
  admits a claimless token -- no regression.
- A rejected missing-tenant attempt emits the existing ``AuthenticationFailed``
  audit event through the authentication middleware.
- Config plumbing: ``require_tenant`` parses and is inherited by per-issuer
  entries.

Naming: NEUTRAL placeholders only (tenant:a / tenant:b, issuer.example.com).
"""

from __future__ import annotations

import pytest

from mcp_hangar.auth.config import OIDCAuthConfig, OIDCIssuerConfig, parse_auth_config
from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig
from mcp_hangar.auth.infrastructure.middleware import AuthenticationMiddleware
from mcp_hangar.domain.contracts.authentication import AuthRequest, ITokenValidator
from mcp_hangar.domain.exceptions import InvalidCredentialsError
from mcp_hangar.fastmcp_server.asgi import _principal_to_identity_context

# ---------------------------------------------------------------------------
# Neutral placeholders.
# ---------------------------------------------------------------------------

ISSUER = "https://issuer.example.com"
AUDIENCE = "mcp-hangar"
TENANT_A = "tenant:a"
TENANT_B = "tenant:b"
SUBJECT = "user-123"


# ---------------------------------------------------------------------------
# Test helpers -- a stub validator that returns canned claims (no JWKS/network).
# ---------------------------------------------------------------------------


class _StubValidator(ITokenValidator):
    """Returns pre-canned claims for any token. Signature is never checked."""

    def __init__(self, claims: dict) -> None:
        self._claims = claims

    def validate(self, token: str) -> dict:
        return dict(self._claims)


def _make_authenticator(*, require_tenant: bool, claims: dict) -> JWTAuthenticator:
    config = OIDCConfig(
        issuer=ISSUER,
        audience=AUDIENCE,
        require_tenant=require_tenant,
    )
    return JWTAuthenticator(config, _StubValidator(claims))


def _bearer_request(headers: dict[str, str] | None = None) -> AuthRequest:
    merged = {"Authorization": "Bearer dummy.jwt.token"}
    if headers:
        merged.update(headers)
    return AuthRequest(headers=merged, source_ip="203.0.113.7", method="POST", path="/mcp")


def _base_claims(**overrides) -> dict:
    # iat/exp within the default max lifetime so the lifetime gate passes and the
    # tenant gate is what these tests actually exercise.
    now = 1_700_000_000
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": SUBJECT, "iat": now, "exp": now + 300}
    claims.update(overrides)
    return claims


# ---------------------------------------------------------------------------
# 1. Multi-tenant mode + missing/empty tenant claim -> rejected (fail-closed).
# ---------------------------------------------------------------------------


def test_multi_tenant_missing_tenant_claim_is_rejected() -> None:
    auth = _make_authenticator(require_tenant=True, claims=_base_claims())  # no tenant_id
    with pytest.raises(InvalidCredentialsError):
        auth.authenticate(_bearer_request())


def test_multi_tenant_empty_tenant_claim_is_rejected() -> None:
    auth = _make_authenticator(require_tenant=True, claims=_base_claims(tenant_id=""))
    with pytest.raises(InvalidCredentialsError):
        auth.authenticate(_bearer_request())


def test_multi_tenant_whitespace_tenant_claim_is_rejected() -> None:
    auth = _make_authenticator(require_tenant=True, claims=_base_claims(tenant_id="   "))
    with pytest.raises(InvalidCredentialsError):
        auth.authenticate(_bearer_request())


# ---------------------------------------------------------------------------
# 2. Effective tenant is the claim; a client cannot override it.
# ---------------------------------------------------------------------------


def test_tenant_claim_becomes_effective_tenant() -> None:
    auth = _make_authenticator(require_tenant=True, claims=_base_claims(tenant_id=TENANT_A))
    principal = auth.authenticate(_bearer_request())
    assert principal.tenant_id == TENANT_A


def test_client_cannot_override_effective_tenant_via_headers() -> None:
    """A token scoped to tenant A must act as A even when the client asserts B.

    The token claim says tenant A. The client tries to override to tenant B via
    plausible header names. The effective tenant (Principal + bridged
    IdentityContext) must remain A -- no header path feeds it.
    """
    auth = _make_authenticator(require_tenant=True, claims=_base_claims(tenant_id=TENANT_A))
    hostile_headers = {
        "X-Tenant-Id": TENANT_B,
        "X-Tenant": TENANT_B,
        "tenant_id": TENANT_B,
    }
    principal = auth.authenticate(_bearer_request(hostile_headers))

    # Effective tenant on the principal is the token claim, not the header.
    assert principal.tenant_id == TENANT_A

    # And it stays A across the bridge into the request identity context, which
    # is what every downstream per-tenant enforcement point reads.
    identity = _principal_to_identity_context(principal)
    assert identity.caller.tenant_id == TENANT_A


# ---------------------------------------------------------------------------
# 3. Single-tenant / no-OIDC mode -- no regression.
# ---------------------------------------------------------------------------


def test_single_tenant_mode_admits_claimless_token() -> None:
    auth = _make_authenticator(require_tenant=False, claims=_base_claims())  # no tenant_id
    principal = auth.authenticate(_bearer_request())
    assert principal.id.value == SUBJECT
    assert principal.tenant_id is None


def test_single_tenant_mode_passes_through_tenant_when_present() -> None:
    auth = _make_authenticator(require_tenant=False, claims=_base_claims(tenant_id=TENANT_A))
    principal = auth.authenticate(_bearer_request())
    assert principal.tenant_id == TENANT_A


# ---------------------------------------------------------------------------
# 4. Rejection emits the existing audit event.
# ---------------------------------------------------------------------------


def test_missing_tenant_rejection_emits_audit_event() -> None:
    from mcp_hangar.domain.events import AuthenticationFailed

    events: list[object] = []

    def _publish(event: object) -> None:
        events.append(event)

    auth = _make_authenticator(require_tenant=True, claims=_base_claims())  # no tenant_id
    middleware = AuthenticationMiddleware(
        authenticators=[auth],
        allow_anonymous=False,
        event_publisher=_publish,
    )

    with pytest.raises(InvalidCredentialsError):
        middleware.authenticate(_bearer_request())

    failed = [e for e in events if isinstance(e, AuthenticationFailed)]
    assert len(failed) == 1
    assert failed[0].auth_method == "JWTAuthenticator"


# ---------------------------------------------------------------------------
# 5. Config plumbing -- require_tenant parses and is inherited per issuer.
# ---------------------------------------------------------------------------


def test_require_tenant_defaults_false() -> None:
    assert OIDCAuthConfig().require_tenant is False
    assert OIDCIssuerConfig().require_tenant is False
    assert OIDCConfig(issuer=ISSUER, audience=AUDIENCE).require_tenant is False


def test_parse_auth_config_reads_require_tenant() -> None:
    cfg = parse_auth_config(
        {
            "oidc": {
                "enabled": True,
                "issuer": ISSUER,
                "audience": AUDIENCE,
                "require_tenant": True,
            }
        }
    )
    assert cfg.oidc.require_tenant is True
    # Legacy single-issuer synthesis carries the flag into the resolved entry.
    resolved = cfg.oidc.resolved_issuers()
    assert resolved and all(entry.require_tenant is True for entry in resolved)


def test_per_issuer_inherits_top_level_require_tenant() -> None:
    cfg = parse_auth_config(
        {
            "oidc": {
                "enabled": True,
                "require_tenant": True,
                "issuers": [
                    {"issuer": ISSUER, "audience": AUDIENCE},  # inherits require_tenant
                    {"issuer": "https://other.example.com", "audience": "aud-2", "require_tenant": False},
                ],
            }
        }
    )
    by_issuer = {e.issuer: e for e in cfg.oidc.resolved_issuers()}
    assert by_issuer[ISSUER].require_tenant is True
    assert by_issuer["https://other.example.com"].require_tenant is False
