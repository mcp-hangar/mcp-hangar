"""Tests for JWT identity extractor."""

import pytest

from mcp_hangar.infrastructure.identity.jwt_extractor import JWTIdentityExtractor


def test_mixed_algorithm_families_raises() -> None:
    """Mixed HS and RS algorithm families must raise ValueError (confusion attack prevention)."""
    with pytest.raises(ValueError, match="Mixed JWT algorithm families"):
        JWTIdentityExtractor(
            secret_or_key="my-secret",
            algorithms=["HS256", "RS256"],
        )


def test_single_hs_family_ok() -> None:
    extractor = JWTIdentityExtractor(secret_or_key="my-secret", algorithms=["HS256"])
    assert extractor is not None


def test_single_asym_family_ok() -> None:
    extractor = JWTIdentityExtractor(secret_or_key="-----BEGIN RSA PUBLIC KEY-----", algorithms=["RS256"])
    assert extractor is not None


def test_tenant_claim_maps_to_tenant_id() -> None:
    """JWT carrying tenant_claim yields CallerIdentity with tenant_id set."""
    try:
        import jwt as pyjwt
    except ImportError:
        pytest.skip("PyJWT not installed")

    secret = "test-secret"
    token = pyjwt.encode(
        {"sub": "user-1", "tenant_id": "tenant-abc"},
        secret,
        algorithm="HS256",
    )
    extractor = JWTIdentityExtractor(secret_or_key=secret)
    ctx = extractor.extract({"Authorization": f"Bearer {token}"})
    assert ctx is not None
    assert ctx.caller.tenant_id == "tenant-abc"


def test_absent_tenant_claim_yields_none() -> None:
    """Absent tenant claim results in tenant_id=None (no error)."""
    try:
        import jwt as pyjwt
    except ImportError:
        pytest.skip("PyJWT not installed")

    secret = "test-secret"
    token = pyjwt.encode({"sub": "user-1"}, secret, algorithm="HS256")
    extractor = JWTIdentityExtractor(secret_or_key=secret)
    ctx = extractor.extract({"Authorization": f"Bearer {token}"})
    assert ctx is not None
    assert ctx.caller.tenant_id is None


def test_custom_tenant_claim_name() -> None:
    """Custom tenant_claim parameter is honoured."""
    try:
        import jwt as pyjwt
    except ImportError:
        pytest.skip("PyJWT not installed")

    secret = "test-secret"
    token = pyjwt.encode(
        {"sub": "user-1", "org_id": "org-99"},
        secret,
        algorithm="HS256",
    )
    extractor = JWTIdentityExtractor(secret_or_key=secret, tenant_claim="org_id")
    ctx = extractor.extract({"Authorization": f"Bearer {token}"})
    assert ctx is not None
    assert ctx.caller.tenant_id == "org-99"
