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
