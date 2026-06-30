"""Tier 2 live verification: auth / IdP (JWT/OIDC, multi-issuer, audience, RBAC).

Placeholder skeleton -- these need a running IdP (Keycloak). Reuse
examples/auth-keycloak/docker-compose.yml + realm-export.json as the harness.
Tracked in docs/internal/LIVE_VERIFICATION.md.
"""

import pytest

pytestmark = [pytest.mark.live, pytest.mark.t2]


@pytest.mark.skip(reason="T2 Keycloak harness -- follow-up (see LIVE_VERIFICATION.md)")
def test_unauthenticated_front_door_call_is_denied():
    """Claim: in front_door mode, an unauthenticated request is DENIED (fail-closed)."""


@pytest.mark.skip(reason="T2 Keycloak harness -- follow-up")
def test_valid_oidc_token_authenticates_and_carries_tenant():
    """Claim: a signed OIDC token authenticates; tenant_id is extracted from the claim."""


@pytest.mark.skip(reason="T2 Keycloak harness -- follow-up")
def test_token_from_untrusted_issuer_is_rejected():
    """Claim: with a multi-issuer registry, a token from an untrusted issuer is rejected (#273)."""


@pytest.mark.skip(reason="T2 Keycloak harness -- follow-up")
def test_prm_advertises_trusted_issuers():
    """Claim: GET /.well-known/oauth-protected-resource lists all trusted issuers (RFC 9728)."""
