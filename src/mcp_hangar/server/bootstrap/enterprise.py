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
    behavioral_profiler: Any = None
    schema_tracker: Any = None
    resource_store: Any = None
    report_generator: Any = None


def load_enterprise_modules(
    tier: LicenseTier,
    config: dict,
    event_bus: Any = None,
    event_publisher: Any = None,
) -> EnterpriseComponents:
    """Load enterprise modules based on the validated license tier.

    For COMMUNITY tier, no enterprise imports are attempted.  For PRO and
    ENTERPRISE tiers, auth and behavioral modules are loaded when available.
    Each import block is isolated so a single missing module does not prevent
    others from loading.

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

    db_path = config.get("event_store", {}).get("path", "data/events.db")
    behavioral_config = config.get("behavioral", {})

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

    # -- Behavioral modules (PRO and ENTERPRISE) --
    if tier.includes_behavioral():
        # Behavioral profiler
        try:
            from enterprise.behavioral.bootstrap import bootstrap_behavioral

            components.behavioral_profiler = bootstrap_behavioral(
                db_path=db_path,
                config=behavioral_config,
                event_bus=event_bus,
            )
        except ImportError:
            logger.warning("enterprise_behavioral_not_installed", tier=tier.value, component="profiler")

        # Schema tracker
        try:
            from enterprise.behavioral.bootstrap import bootstrap_schema_tracker

            components.schema_tracker = bootstrap_schema_tracker(db_path=db_path)
        except ImportError:
            logger.warning("enterprise_behavioral_not_installed", tier=tier.value, component="schema_tracker")

        # Resource store
        try:
            from enterprise.behavioral.bootstrap import bootstrap_resource_monitor

            components.resource_store = bootstrap_resource_monitor(
                db_path=db_path,
                config=behavioral_config,
            )
        except ImportError:
            logger.warning("enterprise_behavioral_not_installed", tier=tier.value, component="resource_store")

        # Report generator (depends on schema_tracker + resource_store)
        try:
            from enterprise.behavioral.bootstrap import bootstrap_report_generator

            components.report_generator = bootstrap_report_generator(
                db_path=db_path,
                schema_tracker=components.schema_tracker,
                resource_store=components.resource_store,
            )
        except ImportError:
            logger.warning("enterprise_behavioral_not_installed", tier=tier.value, component="report_generator")

    loaded_flags = {
        "auth": components.auth_components is not None,
        "behavioral": components.behavioral_profiler is not None,
        "schema_tracker": components.schema_tracker is not None,
        "resource_store": components.resource_store is not None,
        "report_generator": components.report_generator is not None,
    }
    logger.info("enterprise_modules_loaded", tier=tier.value, **loaded_flags)

    return components
