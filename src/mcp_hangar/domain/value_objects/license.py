"""License tier domain value object.

Defines the LicenseTier enum representing the three license levels:
COMMUNITY (free/default), PRO (auth + behavioral), and ENTERPRISE (all modules).

This is an MIT-licensed domain primitive. The actual license key validation
lives in enterprise/auth/license.py (BSL 1.1).
"""

from enum import Enum


class LicenseTier(Enum):
    """License tier that determines which enterprise modules are available.

    COMMUNITY: Free tier, no enterprise modules. Default when no key is present.
    PRO: Includes auth stack and behavioral profiling.
    ENTERPRISE: All enterprise modules including full dashboard, compliance, semantic.
    """

    COMMUNITY = "community"
    PRO = "pro"
    ENTERPRISE = "enterprise"

    def __str__(self) -> str:
        return self.value

    def includes_auth(self) -> bool:
        """Whether this tier includes the authentication and authorization stack."""
        return self in (LicenseTier.PRO, LicenseTier.ENTERPRISE)

    def includes_behavioral(self) -> bool:
        """Whether this tier includes behavioral profiling and deviation detection."""
        return self in (LicenseTier.PRO, LicenseTier.ENTERPRISE)

    def includes_full_enterprise(self) -> bool:
        """Whether this tier includes all enterprise modules (compliance, semantic, etc.)."""
        return self is LicenseTier.ENTERPRISE
