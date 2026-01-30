"""Built-in roles and permissions for MCP Hangar.

This module defines the default RBAC configuration with predefined
roles suitable for most use cases. Custom roles can be added via
configuration or the role store API.

Roles follow the principle of least privilege - each role grants
only the permissions needed for its intended purpose.
"""

from ..value_objects import Permission, Role

# =============================================================================
# Predefined Permissions
# =============================================================================

# Provider management permissions
PERMISSION_PROVIDER_CREATE = Permission("provider", "create")
PERMISSION_PROVIDER_READ = Permission("provider", "read")
PERMISSION_PROVIDER_UPDATE = Permission("provider", "update")
PERMISSION_PROVIDER_DELETE = Permission("provider", "delete")
PERMISSION_PROVIDER_LIST = Permission("provider", "list")
PERMISSION_PROVIDER_START = Permission("provider", "start")
PERMISSION_PROVIDER_STOP = Permission("provider", "stop")
PERMISSION_PROVIDER_LOAD = Permission("provider", "load")
PERMISSION_PROVIDER_LOAD_VERIFIED = Permission("provider", "load", "verified")
PERMISSION_PROVIDER_LOAD_ANY = Permission("provider", "load", "any")
PERMISSION_PROVIDER_UNLOAD = Permission("provider", "unload")

# Tool invocation permissions
PERMISSION_TOOL_INVOKE = Permission("tool", "invoke")
PERMISSION_TOOL_LIST = Permission("tool", "list")

# Configuration permissions
PERMISSION_CONFIG_READ = Permission("config", "read")
PERMISSION_CONFIG_UPDATE = Permission("config", "update")

# Audit permissions
PERMISSION_AUDIT_READ = Permission("audit", "read")

# Metrics permissions
PERMISSION_METRICS_READ = Permission("metrics", "read")

# Group management permissions
PERMISSION_GROUP_CREATE = Permission("group", "create")
PERMISSION_GROUP_READ = Permission("group", "read")
PERMISSION_GROUP_UPDATE = Permission("group", "update")
PERMISSION_GROUP_DELETE = Permission("group", "delete")
PERMISSION_GROUP_LIST = Permission("group", "list")

# Discovery permissions
PERMISSION_DISCOVERY_READ = Permission("discovery", "read")
PERMISSION_DISCOVERY_TRIGGER = Permission("discovery", "trigger")
PERMISSION_DISCOVERY_APPROVE = Permission("discovery", "approve")

# Admin wildcard permission
PERMISSION_ADMIN_ALL = Permission("*", "*")

# Permission registry for easy lookup
PERMISSIONS: dict[str, Permission] = {
    # Provider
    "provider:create": PERMISSION_PROVIDER_CREATE,
    "provider:read": PERMISSION_PROVIDER_READ,
    "provider:update": PERMISSION_PROVIDER_UPDATE,
    "provider:delete": PERMISSION_PROVIDER_DELETE,
    "provider:list": PERMISSION_PROVIDER_LIST,
    "provider:start": PERMISSION_PROVIDER_START,
    "provider:stop": PERMISSION_PROVIDER_STOP,
    "provider:load": PERMISSION_PROVIDER_LOAD,
    "provider:load:verified": PERMISSION_PROVIDER_LOAD_VERIFIED,
    "provider:load:any": PERMISSION_PROVIDER_LOAD_ANY,
    "provider:unload": PERMISSION_PROVIDER_UNLOAD,
    # Tool
    "tool:invoke": PERMISSION_TOOL_INVOKE,
    "tool:list": PERMISSION_TOOL_LIST,
    # Config
    "config:read": PERMISSION_CONFIG_READ,
    "config:update": PERMISSION_CONFIG_UPDATE,
    # Audit
    "audit:read": PERMISSION_AUDIT_READ,
    # Metrics
    "metrics:read": PERMISSION_METRICS_READ,
    # Group
    "group:create": PERMISSION_GROUP_CREATE,
    "group:read": PERMISSION_GROUP_READ,
    "group:update": PERMISSION_GROUP_UPDATE,
    "group:delete": PERMISSION_GROUP_DELETE,
    "group:list": PERMISSION_GROUP_LIST,
    # Discovery
    "discovery:read": PERMISSION_DISCOVERY_READ,
    "discovery:trigger": PERMISSION_DISCOVERY_TRIGGER,
    "discovery:approve": PERMISSION_DISCOVERY_APPROVE,
    # Admin
    "admin:*": PERMISSION_ADMIN_ALL,
}


# =============================================================================
# Built-in Roles
# =============================================================================

ROLE_ADMIN = Role(
    name="admin",
    description="Full administrative access to all resources",
    permissions=frozenset([PERMISSION_ADMIN_ALL]),
)

ROLE_PROVIDER_ADMIN = Role(
    name="provider-admin",
    description="Manage providers and invoke tools",
    permissions=frozenset(
        [
            PERMISSION_PROVIDER_CREATE,
            PERMISSION_PROVIDER_READ,
            PERMISSION_PROVIDER_UPDATE,
            PERMISSION_PROVIDER_DELETE,
            PERMISSION_PROVIDER_LIST,
            PERMISSION_PROVIDER_START,
            PERMISSION_PROVIDER_STOP,
            PERMISSION_PROVIDER_LOAD,
            PERMISSION_PROVIDER_LOAD_VERIFIED,
            PERMISSION_PROVIDER_LOAD_ANY,
            PERMISSION_PROVIDER_UNLOAD,
            PERMISSION_TOOL_INVOKE,
            PERMISSION_TOOL_LIST,
            PERMISSION_METRICS_READ,
            PERMISSION_GROUP_CREATE,
            PERMISSION_GROUP_READ,
            PERMISSION_GROUP_UPDATE,
            PERMISSION_GROUP_DELETE,
            PERMISSION_GROUP_LIST,
            PERMISSION_DISCOVERY_READ,
            PERMISSION_DISCOVERY_TRIGGER,
            PERMISSION_DISCOVERY_APPROVE,
        ]
    ),
)

ROLE_DEVELOPER = Role(
    name="developer",
    description="Invoke tools and view providers",
    permissions=frozenset(
        [
            PERMISSION_PROVIDER_READ,
            PERMISSION_PROVIDER_LIST,
            PERMISSION_PROVIDER_START,  # Can start providers on-demand
            PERMISSION_PROVIDER_LOAD,
            PERMISSION_PROVIDER_LOAD_VERIFIED,  # Can load verified providers
            PERMISSION_PROVIDER_UNLOAD,
            PERMISSION_TOOL_INVOKE,
            PERMISSION_TOOL_LIST,
            PERMISSION_GROUP_READ,
            PERMISSION_GROUP_LIST,
            PERMISSION_DISCOVERY_READ,
        ]
    ),
)

ROLE_VIEWER = Role(
    name="viewer",
    description="Read-only access to providers and tools",
    permissions=frozenset(
        [
            PERMISSION_PROVIDER_READ,
            PERMISSION_PROVIDER_LIST,
            PERMISSION_TOOL_LIST,
            PERMISSION_METRICS_READ,
            PERMISSION_GROUP_READ,
            PERMISSION_GROUP_LIST,
            PERMISSION_DISCOVERY_READ,
        ]
    ),
)

ROLE_AUDITOR = Role(
    name="auditor",
    description="Read-only access to audit logs and metrics",
    permissions=frozenset(
        [
            PERMISSION_AUDIT_READ,
            PERMISSION_METRICS_READ,
            PERMISSION_PROVIDER_LIST,
            PERMISSION_GROUP_LIST,
            PERMISSION_DISCOVERY_READ,
        ]
    ),
)

ROLE_SERVICE_ACCOUNT = Role(
    name="service-account",
    description="Default role for service accounts - tool invocation only",
    permissions=frozenset(
        [
            PERMISSION_PROVIDER_READ,
            PERMISSION_PROVIDER_LIST,
            PERMISSION_TOOL_INVOKE,
            PERMISSION_TOOL_LIST,
        ]
    ),
)

# Role registry for easy lookup
BUILTIN_ROLES: dict[str, Role] = {
    "admin": ROLE_ADMIN,
    "provider-admin": ROLE_PROVIDER_ADMIN,
    "developer": ROLE_DEVELOPER,
    "viewer": ROLE_VIEWER,
    "auditor": ROLE_AUDITOR,
    "service-account": ROLE_SERVICE_ACCOUNT,
}


def get_builtin_role(name: str) -> Role | None:
    """Get a built-in role by name.

    Args:
        name: Name of the built-in role.

    Returns:
        Role if found, None otherwise.
    """
    return BUILTIN_ROLES.get(name)


def get_permission(key: str) -> Permission | None:
    """Get a predefined permission by key.

    Args:
        key: Permission key in format 'resource:action'.

    Returns:
        Permission if found, None otherwise.
    """
    return PERMISSIONS.get(key)


def list_builtin_roles() -> list[str]:
    """Get list of all built-in role names.

    Returns:
        List of role names.
    """
    return list(BUILTIN_ROLES.keys())


def list_permissions() -> list[str]:
    """Get list of all predefined permission keys.

    Returns:
        List of permission keys.
    """
    return list(PERMISSIONS.keys())
