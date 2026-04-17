# pyright: reportAssignmentType=false, reportConstantRedefinition=false, reportUnusedParameter=false

"""Built-in roles stub for core domain.

Role definitions have been moved to ``enterprise/auth/roles.py``
under BSL 1.1.

This stub re-exports from enterprise when available. When enterprise
is not installed, all role-related symbols resolve to empty defaults
to allow core to function without auth features.
"""

try:
    from enterprise.auth.roles import (
        BUILTIN_ROLES,
        get_builtin_role,
        get_permission,
        list_builtin_roles,
        list_permissions,
        PERMISSIONS,
        ROLE_AGENT,
        ROLE_ADMIN,
        ROLE_AUDITOR,
        ROLE_DEVELOPER,
        ROLE_PROVIDER_ADMIN,
        ROLE_VIEWER,
    )
except ImportError:
    from mcp_hangar.domain.value_objects import Permission, Role

    BUILTIN_ROLES: dict[str, "Role"] = {}
    PERMISSIONS: dict[str, "Permission"] = {}

    ROLE_ADMIN = None
    ROLE_AGENT = None
    ROLE_AUDITOR = None
    ROLE_DEVELOPER = None
    ROLE_PROVIDER_ADMIN = None
    ROLE_VIEWER = None

    def get_builtin_role(name: str) -> "Role | None":
        """Return None when enterprise is not installed."""
        return None

    def get_permission(resource: str, action: str, scope: str | None = None) -> "Permission | None":
        """Return None when enterprise is not installed."""
        return None

    def list_builtin_roles() -> "list[Role]":
        """Return empty list when enterprise is not installed."""
        return []

    def list_permissions() -> "list[Permission]":
        """Return empty list when enterprise is not installed."""
        return []


__all__ = [
    "BUILTIN_ROLES",
    "PERMISSIONS",
    "ROLE_AGENT",
    "ROLE_ADMIN",
    "ROLE_AUDITOR",
    "ROLE_DEVELOPER",
    "ROLE_PROVIDER_ADMIN",
    "ROLE_VIEWER",
    "get_builtin_role",
    "get_permission",
    "list_builtin_roles",
    "list_permissions",
]
