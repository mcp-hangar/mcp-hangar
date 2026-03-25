"""Unit tests for LicenseValidator enterprise module.

Tests HMAC-SHA256 key validation, prefix checking, expiry with grace period,
tier extraction, and all failure modes falling back to COMMUNITY.
"""

import base64
import hashlib
import hmac
import json
import time
from unittest.mock import patch

import pytest

from enterprise.auth.license import LicenseValidator, LicenseValidationResult
from mcp_hangar.domain.value_objects.license import LicenseTier


# -- Test helper --

_HMAC_SECRET = b"hangar-v1-license-signing-key"


def _make_valid_key(
    tier: str = "pro",
    org: str = "test-org",
    expires_at: float | None = None,
) -> str:
    """Construct a properly signed hk_v1_ license key for testing."""
    if expires_at is None:
        expires_at = time.time() + 86400  # 24h from now
    payload = {"tier": tier, "org": org, "expires_at": expires_at}
    payload_bytes = json.dumps(payload, sort_keys=True).encode()
    signature = hmac.new(_HMAC_SECRET, payload_bytes, hashlib.sha256).hexdigest()
    payload["signature"] = signature
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    return f"hk_v1_{encoded}"


class TestValidateNoneOrEmpty:
    """No key or empty string returns COMMUNITY with no_license_key error."""

    def test_validate_none_returns_community(self):
        v = LicenseValidator()
        result = v.validate(None)
        assert result.tier == LicenseTier.COMMUNITY
        assert result.error == "no_license_key"

    def test_validate_empty_string_returns_community(self):
        v = LicenseValidator()
        result = v.validate("")
        assert result.tier == LicenseTier.COMMUNITY
        assert result.error == "no_license_key"


class TestValidateBadPrefix:
    """Key without hk_v1_ prefix returns COMMUNITY with bad_prefix error."""

    def test_bad_prefix_returns_community(self):
        v = LicenseValidator()
        result = v.validate("bad_prefix_xxx")
        assert result.tier == LicenseTier.COMMUNITY
        assert result.error == "bad_prefix"


class TestValidateDecodeFailed:
    """Malformed base64 after prefix returns COMMUNITY with decode_failed error."""

    def test_invalid_base64_returns_community(self):
        v = LicenseValidator()
        result = v.validate("hk_v1_not-valid-base64!!!")
        assert result.tier == LicenseTier.COMMUNITY
        assert result.error == "decode_failed"

    def test_valid_base64_invalid_json_returns_community(self):
        v = LicenseValidator()
        encoded = base64.urlsafe_b64encode(b"not json").decode()
        result = v.validate(f"hk_v1_{encoded}")
        assert result.tier == LicenseTier.COMMUNITY
        assert result.error == "decode_failed"


class TestValidateBadSignature:
    """Key with tampered or missing signature returns COMMUNITY."""

    def test_bad_signature_returns_community(self):
        v = LicenseValidator()
        payload = {
            "tier": "pro",
            "org": "test",
            "expires_at": time.time() + 86400,
            "signature": "0" * 64,
        }
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        result = v.validate(f"hk_v1_{encoded}")
        assert result.tier == LicenseTier.COMMUNITY
        assert result.error == "bad_signature"


class TestValidateValidKeys:
    """Properly signed keys with valid expiry extract the correct tier."""

    def test_valid_pro_key(self):
        v = LicenseValidator()
        key = _make_valid_key(tier="pro")
        result = v.validate(key)
        assert result.tier == LicenseTier.PRO
        assert result.grace_period is False
        assert result.error is None

    def test_valid_enterprise_key(self):
        v = LicenseValidator()
        key = _make_valid_key(tier="enterprise")
        result = v.validate(key)
        assert result.tier == LicenseTier.ENTERPRISE
        assert result.grace_period is False
        assert result.error is None

    def test_valid_community_key(self):
        v = LicenseValidator()
        key = _make_valid_key(tier="community")
        result = v.validate(key)
        assert result.tier == LicenseTier.COMMUNITY
        assert result.grace_period is False
        assert result.error is None


class TestValidateGracePeriod:
    """Expired key within 7-day grace returns tier with grace_period=True."""

    def test_expired_within_grace_returns_tier_with_grace_flag(self):
        v = LicenseValidator()
        # Expired 3 days ago (within 7-day grace)
        expired_at = time.time() - (3 * 86400)
        key = _make_valid_key(tier="pro", expires_at=expired_at)
        result = v.validate(key)
        assert result.tier == LicenseTier.PRO
        assert result.grace_period is True
        assert result.error is None

    def test_expired_beyond_grace_returns_community(self):
        v = LicenseValidator()
        # Expired 10 days ago (beyond 7-day grace)
        expired_at = time.time() - (10 * 86400)
        key = _make_valid_key(tier="enterprise", expires_at=expired_at)
        result = v.validate(key)
        assert result.tier == LicenseTier.COMMUNITY
        assert result.error == "expired_beyond_grace"

    @patch("enterprise.auth.license.time")
    def test_grace_period_boundary_exactly_7_days(self, mock_time):
        """Key expired exactly 7 days ago is still within grace."""
        now = 1700000000.0
        mock_time.time.return_value = now
        v = LicenseValidator()
        # Expired exactly 7 days ago
        expired_at = now - (7 * 86400)
        key = _make_valid_key(tier="pro", expires_at=expired_at)
        result = v.validate(key)
        assert result.tier == LicenseTier.PRO
        assert result.grace_period is True


class TestValidateUnknownTier:
    """Key with unrecognized tier string returns COMMUNITY."""

    def test_unknown_tier_returns_community(self):
        v = LicenseValidator()
        key = _make_valid_key(tier="platinum")
        result = v.validate(key)
        assert result.tier == LicenseTier.COMMUNITY
        assert result.error == "unknown_tier"


class TestValidateOrgExtraction:
    """Valid key with org field returns the org in the result."""

    def test_org_extracted_from_payload(self):
        v = LicenseValidator()
        key = _make_valid_key(tier="enterprise", org="acme-corp")
        result = v.validate(key)
        assert result.org == "acme-corp"

    def test_missing_org_defaults_to_empty(self):
        v = LicenseValidator()
        # Build key without org field
        expires_at = time.time() + 86400
        payload = {"tier": "pro", "expires_at": expires_at}
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        signature = hmac.new(_HMAC_SECRET, payload_bytes, hashlib.sha256).hexdigest()
        payload["signature"] = signature
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        key = f"hk_v1_{encoded}"
        result = v.validate(key)
        assert result.org == ""


class TestLicenseValidationResultDefaults:
    """LicenseValidationResult dataclass has correct defaults."""

    def test_default_values(self):
        result = LicenseValidationResult(tier=LicenseTier.COMMUNITY)
        assert result.org == ""
        assert result.expires_at == 0.0
        assert result.grace_period is False
        assert result.error is None
