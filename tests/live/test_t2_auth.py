"""Tier 2 live verification: auth / IdP (JWT/OIDC, multi-issuer, audience, RBAC).

These are BLACK-BOX tests against a REAL Keycloak (issuer A = realm
``mcp-hangar``, issuer B = realm ``mcp-hangar-b``) and a REAL ``mcp-hangar``
process with OIDC auth enabled in ``front_door`` mode. They prove the security
trust-boundary claims tracked in ``docs/internal/LIVE_VERIFICATION.md``:

1. front_door + auth: an UNAUTHENTICATED request is denied (fail-closed).
2. a real signed OIDC token authenticates AND carries a ``tenant_id`` (proven
   fail-closed via ``require_tenant``: a token with no tenant is rejected).
3. a token from an UNTRUSTED issuer is rejected (multi-issuer registry miss).
4. a real token whose ``aud`` does not match the expected resource is rejected
   (RFC 8707 resource binding).
5. the PRM endpoint advertises BOTH trusted issuers and the resource (RFC 9728).

Multi-issuer is also proven positively: a realm-B token is accepted by the same
hangar.

The fixtures (see ``conftest.py``) are skip-safe: if Keycloak/Docker is absent
the whole module SKIPs rather than fails. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t2" -o addopts=""
"""

from __future__ import annotations

import time

import httpx
import jwt
import pytest

pytestmark = [pytest.mark.live, pytest.mark.t2]

# An authenticated (non-skipped) API surface: auth enforcement runs before the
# handler, so a missing/invalid token is rejected at the trust boundary and a
# valid token reaches ``/api/system/me`` which echoes the auth status.
_AUTHED_PATH = "/api/system/me"


def test_unauthenticated_front_door_call_is_denied(hangar_oidc: str) -> None:
    """Claim: in front_door mode, an unauthenticated request is DENIED (fail-closed)."""
    resp = httpx.get(f"{hangar_oidc}{_AUTHED_PATH}", timeout=10.0)
    assert resp.status_code == 401, resp.text
    # RFC 6750/9728: a Bearer challenge is advertised on the fail-closed response.
    assert "www-authenticate" in {k.lower() for k in resp.headers}


def test_valid_oidc_token_authenticates_and_carries_tenant(hangar_oidc: str, keycloak_token) -> None:
    """Claim: a signed OIDC token authenticates; tenant_id is extracted from the claim.

    The hangar runs with ``require_tenant`` on, so authentication SUCCEEDS only
    when a tenant claim is present and extracted from the validated token. A real
    realm-A ``developer`` token carries ``tenant_id=acme`` (Keycloak user-attribute
    mapper), so a 200 here proves both authentication and tenant extraction.
    """
    token = keycloak_token(realm="mcp-hangar", username="developer", password="dev123")
    resp = httpx.get(
        f"{hangar_oidc}{_AUTHED_PATH}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("authenticated") is True, body
    assert body.get("principal"), body


def test_realm_b_token_is_accepted(hangar_oidc: str, keycloak_token) -> None:
    """Claim (multi-issuer positive): a token from the SECOND trusted issuer is accepted."""
    token = keycloak_token(realm="mcp-hangar-b", username="developer", password="dev123")
    resp = httpx.get(
        f"{hangar_oidc}{_AUTHED_PATH}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("authenticated") is True, resp.text


def test_token_from_untrusted_issuer_is_rejected(hangar_oidc: str) -> None:
    """Claim: with a multi-issuer registry, a token from an untrusted issuer is rejected (#273).

    A self-signed JWT whose ``iss`` is neither trusted realm never matches the
    issuer registry, so it is rejected before any signature check.
    """
    now = int(time.time())
    forged = jwt.encode(
        {
            "iss": "https://untrusted.example.invalid/realms/rogue",
            "sub": "attacker",
            "aud": "mcp-hangar",
            "tenant_id": "acme",
            "iat": now,
            "exp": now + 300,
        },
        "attacker-controlled-secret-key-not-trusted-by-hangar",
        algorithm="HS256",
    )
    resp = httpx.get(
        f"{hangar_oidc}{_AUTHED_PATH}",
        headers={"Authorization": f"Bearer {forged}"},
        timeout=10.0,
    )
    assert resp.status_code == 401, resp.text
    assert "www-authenticate" in {k.lower() for k in resp.headers}


def test_aud_mismatch_is_rejected(hangar_oidc_wrong_audience: str, keycloak_token) -> None:
    """Claim: a real token whose aud != expected resource is rejected (RFC 8707).

    ``hangar_oidc_wrong_audience`` expects resource ``urn:mcp-hangar:other-resource``;
    a real realm-A token carries ``aud=mcp-hangar`` and so fails audience validation.
    """
    token = keycloak_token(realm="mcp-hangar", username="developer", password="dev123")
    resp = httpx.get(
        f"{hangar_oidc_wrong_audience}{_AUTHED_PATH}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    assert resp.status_code == 401, resp.text


def test_prm_advertises_trusted_issuers(hangar_oidc: str, keycloak_base_url: str) -> None:
    """Claim: GET /.well-known/oauth-protected-resource lists all trusted issuers (RFC 9728)."""
    resp = httpx.get(f"{hangar_oidc}/.well-known/oauth-protected-resource", timeout=10.0)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    servers = set(body.get("authorization_servers", []))
    issuer_a = f"{keycloak_base_url}/realms/mcp-hangar"
    issuer_b = f"{keycloak_base_url}/realms/mcp-hangar-b"
    assert issuer_a in servers, body
    assert issuer_b in servers, body
    # RFC 9728: the resource identifier is advertised too.
    assert body.get("resource") == "mcp-hangar", body
