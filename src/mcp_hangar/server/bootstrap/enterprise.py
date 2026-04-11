"""Enterprise module loading, gated by license tier.

This module centralizes all enterprise package imports into a single
``load_enterprise_modules()`` function.  The caller provides the validated
:class:`LicenseTier` and receives an :class:`EnterpriseComponents` container
whose fields are ``None`` for any modules that are unavailable (wrong tier or
package not installed).

MIT licensed -- all ``enterprise.*`` imports MUST be inside ``try/except
ImportError`` blocks so the CI import-boundary check passes.
"""

from dataclasses import dataclass
from typing import Any

from ...domain.value_objects.license import LicenseTier
from ...logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class EnterpriseComponents:
    """Container for all enterprise module instances loaded based on license tier."""

    license_tier: LicenseTier = LicenseTier.COMMUNITY
    auth_components: Any = None
    approval_service: Any = None


def load_enterprise_modules(
    tier: LicenseTier,
    config: dict,
    event_bus: Any = None,
    event_publisher: Any = None,
) -> EnterpriseComponents:
    """Load enterprise modules based on the validated license tier.

    For COMMUNITY tier, no enterprise imports are attempted.  For PRO and
    ENTERPRISE tiers, auth modules are loaded when available.

    Args:
        tier: Validated license tier from LicenseValidator.
        config: Full application configuration dictionary.
        event_bus: Optional event bus for enterprise module wiring.
        event_publisher: Optional callable for publishing domain events.

    Returns:
        EnterpriseComponents with populated fields for loaded modules.
    """
    components = EnterpriseComponents(license_tier=tier)

    if tier == LicenseTier.COMMUNITY:
        logger.info("enterprise_modules_skipped", tier="community")
        return components

    # -- Auth modules (PRO and ENTERPRISE) --
    if tier.includes_auth():
        try:
            from enterprise.auth.bootstrap import bootstrap_auth
            from enterprise.auth.config import parse_auth_config

            auth_config = parse_auth_config(config.get("auth"))
            components.auth_components = bootstrap_auth(
                config=auth_config,
                event_publisher=event_publisher,
            )
        except ImportError:
            logger.warning("enterprise_auth_not_installed", tier=tier.value)

    # -- Approval gate (ENTERPRISE only) --
    if tier == LicenseTier.ENTERPRISE:
        try:
            from enterprise.approvals.bootstrap import bootstrap_approvals
            from ...infrastructure.persistence.database import Database, DatabaseConfig

            approval_db = Database(DatabaseConfig(path="data/approvals.db"))
            components.approval_service = bootstrap_approvals(
                database=approval_db,
                event_bus=event_bus,
                config=config,
            )
        except ImportError:
            logger.warning("enterprise_approvals_not_installed", tier=tier.value)

    loaded_flags = {
        "auth": components.auth_components is not None,
        "approvals": components.approval_service is not None,
    }
    logger.info("enterprise_modules_loaded", tier=tier.value, **loaded_flags)

    return components
