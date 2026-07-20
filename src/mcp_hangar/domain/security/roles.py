# pyright: reportAssignmentType=false, reportConstantRedefinition=false, reportUnusedParameter=false

"""Built-in roles stub for core domain.

Role definitions live in ``mcp_hangar/auth/roles.py``.

This stub re-exports from auth when available via importlib. When
auth is not installed, all role-related symbols resolve to empty
defaults to allow core to function without auth features.
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_hangar.domain.value_objects import Permission, Role

_ENTERPRISE_ROLE_NAMES = (
    "BUILTIN_ROLES",
    "PERMISSIONS",
    "ROLE_ADMIN",
    "ROLE_AUDITOR",
    "ROLE_DEVELOPER",
    "ROLE_PROVIDER_ADMIN",
    "ROLE_VIEWER",
    "get_builtin_role",
    "get_permission",
    "list_builtin_roles",
    "list_permissions",
)

try:
    _enterprise_roles = importlib.import_module("mcp_hangar.auth.roles")
    BUILTIN_ROLES = _enterprise_roles.BUILTIN_ROLES
    PERMISSIONS = _enterprise_roles.PERMISSIONS
    ROLE_ADMIN = _enterprise_roles.ROLE_ADMIN
    ROLE_AUDITOR = _enterprise_roles.ROLE_AUDITOR
    ROLE_DEVELOPER = _enterprise_roles.ROLE_DEVELOPER
    ROLE_PROVIDER_ADMIN = _enterprise_roles.ROLE_PROVIDER_ADMIN
    ROLE_VIEWER = _enterprise_roles.ROLE_VIEWER
    get_builtin_role = _enterprise_roles.get_builtin_role
    get_permission = _enterprise_roles.get_permission
    list_builtin_roles = _enterprise_roles.list_builtin_roles
    list_permissions = _enterprise_roles.list_permissions
except ImportError:
    from mcp_hangar.domain.value_objects import Permission, Role

    BUILTIN_ROLES: dict[str, "Role"] = {}  # type: ignore[no-redef]  # fallback stubs when enterprise not installed
    PERMISSIONS: dict[str, "Permission"] = {}  # type: ignore[no-redef]  # fallback stubs when enterprise not installed

    ROLE_ADMIN = None
    ROLE_AUDITOR = None
    ROLE_DEVELOPER = None
    ROLE_PROVIDER_ADMIN = None
    ROLE_VIEWER = None

    def get_builtin_role(name: str) -> "Role | None":
        return None

    def get_permission(key: str) -> "Permission | None":
        return None

    def list_builtin_roles() -> list[str]:
        return []

    def list_permissions() -> list[str]:
        return []


__all__ = [
    "BUILTIN_ROLES",
    "PERMISSIONS",
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
