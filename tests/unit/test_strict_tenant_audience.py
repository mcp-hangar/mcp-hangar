"""Strict per-tenant audience binding (issue #373, RFC 8707).

These tests pin the opt-in strict mode that makes cross-tenant token replay
structurally impossible at the TOKEN layer: a token whose ``aud`` is tenant A's
resource is rejected when its claimed tenant maps to a different resource (or to
no resource at all), independent of the ``tenant_id`` claim.

Design under test (mapping shape chosen for #373):
- An EXPLICIT config map ``tenant_audiences: {tenant_id -> resource_uri}`` (auditable),
  gated by an opt-in flag ``strict_tenant_audience`` (default False).
- Strict ON: token ``aud`` MUST equal the resource mapped to the claimed tenant;
  a tenant absent from the map is rejected fail-closed -- the global ``audience``
  is never a fallback.
- Strict OFF (default): behavior is exactly as post-#312 (single global audience +
  claim), so there is no regression.

Scope proven here is deterministic (a stub validator returns canned claims, so
signature/JWKS verification is never exercised); the strict tenant<->aud binding
is enforced in ``JWTAuthenticator`` and is what these tests target.

Naming: NEUTRAL placeholders only (tenant:a / tenant:b, issuer.example.com).
"""

from __future__ import annotations

import pytest
import structlog

from mcp_hangar.auth.config import OIDCAuthConfig, OIDCIssuerConfig, parse_auth_config
from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig
from mcp_hangar.auth.infrastructure.middleware import AuthenticationMiddleware
from mcp_hangar.domain.contracts.authentication import AuthRequest, ITokenValidator
from mcp_hangar.domain.exceptions import InvalidCredentialsError

# ---------------------------------------------------------------------------
# Neutral placeholders.
# ---------------------------------------------------------------------------

ISSUER = "https://issuer.example.com"
GLOBAL_AUDIENCE = "mcp-hangar"
RESOURCE_A = "https://hangar.example.com/tenant-a"
RESOURCE_B = "https://hangar.example.com/tenant-b"
TENANT_A = "tenant:a"
TENANT_B = "tenant:b"
SUBJECT = "user-123"

TENANT_AUDIENCES = {TENANT_A: RESOURCE_A, TENANT_B: RESOURCE_B}


# ---------------------------------------------------------------------------
# Test helpers -- a stub validator that returns canned claims (no JWKS/network).
# ---------------------------------------------------------------------------


class _StubValidator(ITokenValidator):
    """Returns pre-canned claims for any token. Signature is never checked."""

    def __init__(self, claims: dict) -> None:
        self._claims = claims

    def validate(self, token: str) -> dict:
        return dict(self._claims)


def _make_authenticator(
    *,
    strict: bool,
    claims: dict,
    tenant_audiences: dict[str, str] | None = None,
    require_tenant: bool = False,
) -> JWTAuthenticator:
    config = OIDCConfig(
        issuer=ISSUER,
        audience=GLOBAL_AUDIENCE,
        require_tenant=require_tenant,
        strict_tenant_audience=strict,
        tenant_audiences=dict(TENANT_AUDIENCES if tenant_audiences is None else tenant_audiences),
    )
    return JWTAuthenticator(config, _StubValidator(claims))


def _bearer_request() -> AuthRequest:
    return AuthRequest(
        headers={"Authorization": "Bearer dummy.jwt.token"},
        source_ip="203.0.113.7",
        method="POST",
        path="/mcp",
    )


def _base_claims(*, aud, **overrides) -> dict:
    # iat/exp within the default max lifetime so the lifetime gate passes and the
    # tenant<->audience gate is what these tests actually exercise.
    now = 1_700_000_000
    claims = {"iss": ISSUER, "aud": aud, "sub": SUBJECT, "iat": now, "exp": now + 300}
    claims.update(overrides)
    return claims


# ---------------------------------------------------------------------------
# 1. Strict ON: aud matches the claimed tenant's resource -> accepted.
# ---------------------------------------------------------------------------


def test_strict_matching_tenant_and_aud_is_accepted() -> None:
    auth = _make_authenticator(strict=True, claims=_base_claims(aud=RESOURCE_A, tenant_id=TENANT_A))
    principal = auth.authenticate(_bearer_request())
    assert principal.id.value == SUBJECT
    assert principal.tenant_id == TENANT_A


def test_strict_accepts_aud_as_list_containing_tenant_resource() -> None:
    # RFC 8707 tokens may carry aud as an array; the tenant resource just needs
    # to be present among the audiences.
    auth = _make_authenticator(
        strict=True,
        claims=_base_claims(aud=[GLOBAL_AUDIENCE, RESOURCE_A], tenant_id=TENANT_A),
    )
    principal = auth.authenticate(_bearer_request())
    assert principal.tenant_id == TENANT_A


# ---------------------------------------------------------------------------
# 2. Strict ON: cross-tenant replay -> rejected (aud=A but claim=B).
# ---------------------------------------------------------------------------


def test_strict_rejects_cross_tenant_aud() -> None:
    # Token minted for tenant A's resource, presented with a claim for tenant B.
    auth = _make_authenticator(strict=True, claims=_base_claims(aud=RESOURCE_A, tenant_id=TENANT_B))
    with pytest.raises(InvalidCredentialsError):
        auth.authenticate(_bearer_request())


def test_strict_rejects_aud_matching_no_mapped_tenant() -> None:
    # aud is A's resource but the claim names A -- accepted; flip to confirm the
    # binding is to the CLAIMED tenant, not merely "aud in the known set".
    auth = _make_authenticator(strict=True, claims=_base_claims(aud=RESOURCE_B, tenant_id=TENANT_A))
    with pytest.raises(InvalidCredentialsError):
        auth.authenticate(_bearer_request())


# ---------------------------------------------------------------------------
# 3. Strict ON: tenant with no mapping -> rejected fail-closed.
# ---------------------------------------------------------------------------


def test_strict_rejects_unmapped_tenant() -> None:
    auth = _make_authenticator(strict=True, claims=_base_claims(aud=RESOURCE_A, tenant_id="tenant:unknown"))
    with pytest.raises(InvalidCredentialsError):
        auth.authenticate(_bearer_request())


def test_strict_rejects_missing_tenant_claim() -> None:
    # No tenant claim at all -> no mapping -> reject (does not fall back to the
    # global audience even though aud == GLOBAL_AUDIENCE).
    auth = _make_authenticator(strict=True, claims=_base_claims(aud=GLOBAL_AUDIENCE))
    with pytest.raises(InvalidCredentialsError):
        auth.authenticate(_bearer_request())


def test_strict_with_empty_map_rejects_everything() -> None:
    auth = _make_authenticator(
        strict=True,
        tenant_audiences={},
        claims=_base_claims(aud=RESOURCE_A, tenant_id=TENANT_A),
    )
    with pytest.raises(InvalidCredentialsError):
        auth.authenticate(_bearer_request())


# ---------------------------------------------------------------------------
# 4. Strict OFF (default): behaves exactly as post-#312 -- no regression.
# ---------------------------------------------------------------------------


def test_non_strict_ignores_tenant_audiences_and_accepts() -> None:
    # aud does NOT match any tenant resource, but strict is OFF so the per-tenant
    # binding is not applied (the stub validator stands in for the global-audience
    # signature check that already passed). Cross-tenant claim is irrelevant here.
    auth = _make_authenticator(strict=False, claims=_base_claims(aud=GLOBAL_AUDIENCE, tenant_id=TENANT_A))
    principal = auth.authenticate(_bearer_request())
    assert principal.tenant_id == TENANT_A


def test_non_strict_admits_claimless_token() -> None:
    auth = _make_authenticator(strict=False, claims=_base_claims(aud=GLOBAL_AUDIENCE))
    principal = auth.authenticate(_bearer_request())
    assert principal.tenant_id is None


def test_non_strict_does_not_reject_cross_tenant_aud() -> None:
    # Post-#312 behavior: without strict mode a mismatched aud/claim is not the
    # authenticator's concern (the single global audience was already verified).
    auth = _make_authenticator(strict=False, claims=_base_claims(aud=RESOURCE_A, tenant_id=TENANT_B))
    principal = auth.authenticate(_bearer_request())
    assert principal.tenant_id == TENANT_B


# ---------------------------------------------------------------------------
# 5. Rejection emits the audit event.
# ---------------------------------------------------------------------------


def test_cross_tenant_rejection_emits_structlog_audit_event() -> None:
    auth = _make_authenticator(strict=True, claims=_base_claims(aud=RESOURCE_A, tenant_id=TENANT_B))
    with structlog.testing.capture_logs() as logs:
        with pytest.raises(InvalidCredentialsError):
            auth.authenticate(_bearer_request())
    audit = [e for e in logs if e.get("event") == "jwt_cross_tenant_audience"]
    assert len(audit) == 1
    assert audit[0]["reason"] == "cross_tenant_audience"
    assert audit[0]["tenant_id"] == TENANT_B


def test_unmapped_tenant_rejection_emits_structlog_audit_event() -> None:
    auth = _make_authenticator(strict=True, claims=_base_claims(aud=RESOURCE_A, tenant_id="tenant:unknown"))
    with structlog.testing.capture_logs() as logs:
        with pytest.raises(InvalidCredentialsError):
            auth.authenticate(_bearer_request())
    audit = [e for e in logs if e.get("event") == "jwt_cross_tenant_audience"]
    assert len(audit) == 1
    assert audit[0]["reason"] == "tenant_audience_unmapped"


def test_rejection_emits_middleware_authentication_failed_event() -> None:
    from mcp_hangar.domain.events import AuthenticationFailed

    events: list[object] = []

    def _publish(event: object) -> None:
        events.append(event)

    auth = _make_authenticator(strict=True, claims=_base_claims(aud=RESOURCE_A, tenant_id=TENANT_B))
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
# 6. Config plumbing -- flag + map parse and are inherited per issuer.
# ---------------------------------------------------------------------------


def test_strict_flags_default_false() -> None:
    assert OIDCAuthConfig().strict_tenant_audience is False
    assert OIDCAuthConfig().tenant_audiences == {}
    assert OIDCIssuerConfig().strict_tenant_audience is False
    assert OIDCIssuerConfig().tenant_audiences == {}
    assert OIDCConfig(issuer=ISSUER, audience=GLOBAL_AUDIENCE).strict_tenant_audience is False
    assert OIDCConfig(issuer=ISSUER, audience=GLOBAL_AUDIENCE).tenant_audiences == {}


def test_parse_auth_config_reads_strict_tenant_audience() -> None:
    cfg = parse_auth_config(
        {
            "oidc": {
                "enabled": True,
                "issuer": ISSUER,
                "audience": GLOBAL_AUDIENCE,
                "strict_tenant_audience": True,
                "tenant_audiences": {TENANT_A: RESOURCE_A, TENANT_B: RESOURCE_B},
            }
        }
    )
    assert cfg.oidc.strict_tenant_audience is True
    assert cfg.oidc.tenant_audiences == TENANT_AUDIENCES
    # Legacy single-issuer synthesis carries the flag + map into the resolved entry.
    resolved = cfg.oidc.resolved_issuers()
    assert resolved and all(e.strict_tenant_audience is True for e in resolved)
    assert resolved[0].tenant_audiences == TENANT_AUDIENCES


def test_parse_auth_config_drops_malformed_tenant_audience_entries() -> None:
    cfg = parse_auth_config(
        {
            "oidc": {
                "enabled": True,
                "issuer": ISSUER,
                "strict_tenant_audience": True,
                "tenant_audiences": {TENANT_A: RESOURCE_A, "": RESOURCE_B, TENANT_B: "", "bad": 123},
            }
        }
    )
    # Only the well-formed (non-empty str -> non-empty str) entry survives; a
    # malformed map must never silently admit a tenant with an implicit audience.
    assert cfg.oidc.tenant_audiences == {TENANT_A: RESOURCE_A}


def test_per_issuer_inherits_top_level_strict_tenant_audience() -> None:
    cfg = parse_auth_config(
        {
            "oidc": {
                "enabled": True,
                "strict_tenant_audience": True,
                "tenant_audiences": {TENANT_A: RESOURCE_A},
                "issuers": [
                    {"issuer": ISSUER, "audience": GLOBAL_AUDIENCE},  # inherits both
                    {
                        "issuer": "https://other.example.com",
                        "audience": "aud-2",
                        "strict_tenant_audience": False,
                        "tenant_audiences": {TENANT_B: RESOURCE_B},
                    },
                ],
            }
        }
    )
    by_issuer = {e.issuer: e for e in cfg.oidc.resolved_issuers()}
    assert by_issuer[ISSUER].strict_tenant_audience is True
    assert by_issuer[ISSUER].tenant_audiences == {TENANT_A: RESOURCE_A}
    assert by_issuer["https://other.example.com"].strict_tenant_audience is False
    assert by_issuer["https://other.example.com"].tenant_audiences == {TENANT_B: RESOURCE_B}
