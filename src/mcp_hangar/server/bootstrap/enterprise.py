"""Enterprise module loading, gated by license tier.

This module discovers enterprise bootstrap loaders through Python entry points
instead of importing ``enterprise.*`` modules directly from core code. The
caller provides the validated :class:`LicenseTier` and receives an
:class:`EnterpriseComponents` container whose fields are ``None`` when no
enterprise plugin is installed.
"""

# pyright: reportExplicitAny=false, reportAny=false, reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false

import importlib.metadata
from dataclasses import dataclass
from typing import Any

from ...domain.value_objects.license import LicenseTier
from ...logging_config import get_logger

logger = get_logger(__name__)
ENTERPRISE_ENTRY_POINT_GROUP = "mcp_hangar.enterprise"


@dataclass
class EnterpriseComponents:
    """Container for all enterprise module instances loaded based on license tier."""

    license_tier: LicenseTier = LicenseTier.COMMUNITY
    auth_components: Any = None
    approval_service: Any = None


def _merge_enterprise_components(base: EnterpriseComponents, loaded: EnterpriseComponents) -> None:
    """Merge plugin-provided enterprise components into the result container."""
    if loaded.auth_components is not None:
        base.auth_components = loaded.auth_components
    if loaded.approval_service is not None:
        base.approval_service = loaded.approval_service


def load_enterprise_modules(
    tier: LicenseTier,
    config: dict[str, Any],
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

    entry_points = tuple(importlib.metadata.entry_points(group=ENTERPRISE_ENTRY_POINT_GROUP))
    if not entry_points:
        logger.info("enterprise_modules_unavailable", tier=tier.value, reason="no_entry_points_registered")
        return components

    for entry_point in entry_points:
        loader = entry_point.load()
        loaded_components = loader(tier, config, event_bus, event_publisher)
        if not isinstance(loaded_components, EnterpriseComponents):
            msg = (
                f"Enterprise loader '{entry_point.name}' returned {type(loaded_components).__name__}; "
                "expected EnterpriseComponents"
            )
            raise TypeError(msg)
        _merge_enterprise_components(components, loaded_components)

    loaded_flags = {
        "auth": components.auth_components is not None,
        "approvals": components.approval_service is not None,
    }
    logger.info(
        "enterprise_modules_loaded",
        tier=tier.value,
        entry_points=[entry_point.name for entry_point in entry_points],
        **loaded_flags,
    )

    return components
