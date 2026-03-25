"""License key validation for MCP Hangar enterprise features.

Licensed under BSL 1.1 -- see enterprise/LICENSE.BSL for terms.

Validates license keys in the ``hk_v1_<base64>`` format using HMAC-SHA256
signatures.  Invalid, missing, or expired keys always fall back to
LicenseTier.COMMUNITY so the server can start unconditionally.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field

import structlog

from mcp_hangar.domain.value_objects.license import LicenseTier

logger = structlog.get_logger(__name__)

_HMAC_SECRET = b"hangar-v1-license-signing-key"
_GRACE_PERIOD_DAYS = 7
_PREFIX = "hk_v1_"


@dataclass
class LicenseValidationResult:
    """Result of validating a license key.

    Attributes:
        tier: Resolved license tier (COMMUNITY on any failure).
        org: Organization name extracted from the key payload.
        expires_at: Unix epoch seconds when the key expires.
        grace_period: True when key is expired but within the 7-day grace window.
        error: Short error code if validation failed, None on success.
    """

    tier: LicenseTier
    org: str = ""
    expires_at: float = 0.0
    grace_period: bool = False
    error: str | None = None


class LicenseValidator:
    """Validates ``hk_v1_`` format license keys with HMAC-SHA256 signatures.

    All failure modes return ``LicenseTier.COMMUNITY`` so the server always
    starts.  A 7-day grace period allows expired keys to keep their tier
    temporarily.
    """

    def validate(self, raw_key: str | None) -> LicenseValidationResult:
        """Validate a license key and return the resolved tier.

        Args:
            raw_key: The raw license key string, or None if no key is set.

        Returns:
            LicenseValidationResult with the resolved tier and metadata.
        """
        # -- No key --
        if not raw_key:
            return LicenseValidationResult(tier=LicenseTier.COMMUNITY, error="no_license_key")

        # -- Prefix check --
        if not raw_key.startswith(_PREFIX):
            logger.warning("invalid_license_key", reason="bad_prefix")
            return LicenseValidationResult(tier=LicenseTier.COMMUNITY, error="bad_prefix")

        # -- Decode --
        encoded = raw_key[len(_PREFIX) :]
        try:
            decoded_bytes = base64.urlsafe_b64decode(encoded)
            payload: dict = json.loads(decoded_bytes)
        except Exception:
            logger.warning("invalid_license_key", reason="decode_failed")
            return LicenseValidationResult(tier=LicenseTier.COMMUNITY, error="decode_failed")

        # -- Signature verification --
        received_sig = payload.pop("signature", "")
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        expected_sig = hmac.new(_HMAC_SECRET, payload_bytes, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(received_sig, expected_sig):
            logger.warning("invalid_license_key", reason="bad_signature")
            return LicenseValidationResult(tier=LicenseTier.COMMUNITY, error="bad_signature")

        # -- Expiry + grace period --
        expires_at = float(payload.get("expires_at", 0))
        now = time.time()
        grace_period = False

        if expires_at < now:
            days_expired = (now - expires_at) / 86400
            if days_expired > _GRACE_PERIOD_DAYS:
                logger.warning(
                    "license_expired_beyond_grace",
                    days_expired=round(days_expired, 1),
                    grace_days=_GRACE_PERIOD_DAYS,
                )
                return LicenseValidationResult(tier=LicenseTier.COMMUNITY, error="expired_beyond_grace")
            grace_period = True
            days_remaining = _GRACE_PERIOD_DAYS - days_expired
            logger.warning(
                "license_expired_in_grace",
                days_remaining=round(days_remaining, 1),
                grace_days=_GRACE_PERIOD_DAYS,
            )

        # -- Tier extraction --
        tier_str = payload.get("tier", "")
        try:
            tier = LicenseTier(tier_str)
        except ValueError:
            logger.warning("invalid_license_key", reason="unknown_tier", tier=tier_str)
            return LicenseValidationResult(tier=LicenseTier.COMMUNITY, error="unknown_tier")

        org = payload.get("org", "")
        return LicenseValidationResult(
            tier=tier,
            org=org,
            expires_at=expires_at,
            grace_period=grace_period,
        )
