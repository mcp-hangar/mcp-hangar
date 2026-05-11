"""Authentication and Authorization commands.

Commands represent user intentions for auth operations:
- API Key management (create, revoke)
- Role assignment (assign, revoke)
"""

from dataclasses import dataclass, field
from datetime import datetime

from mcp_hangar.application.commands.commands import Command

# =============================================================================
# API Key Commands
# =============================================================================


@dataclass(frozen=True)
class CreateApiKeyCommand(Command):
    """Command to create a new API key.

    Attributes:
        principal_id: Principal ID the key authenticates as.
        name: Human-readable name for the key.
        created_by: Principal creating the key.
        expires_at: Optional expiration datetime.
        groups: Optional groups to assign.
        tenant_id: Optional tenant for multi-tenancy.
    """

    principal_id: str
    name: str
    created_by: str = "system"
    expires_at: datetime | None = None
    groups: frozenset[str] = field(default_factory=frozenset)
    tenant_id: str | None = None


@dataclass(frozen=True)
class RevokeApiKeyCommand(Command):
    """Command to revoke an API key.

    Attributes:
        key_id: Unique identifier of the key to revoke.
        revoked_by: Principal revoking the key.
        reason: Optional reason for revocation.
    """

    key_id: str
    revoked_by: str = "system"
    reason: str = ""


@dataclass(frozen=True)
class ListApiKeysCommand(Command):
    """Command to list API keys for a principal.

    Attributes:
        principal_id: Principal whose keys to list.
    """

    principal_id: str


# =============================================================================
# Role Commands
# =============================================================================


@dataclass(frozen=True)
class AssignRoleCommand(Command):
    """Command to assign a role to a principal.

    Attributes:
        principal_id: Principal receiving the role.
        role_name: Name of the role to assign.
        scope: Scope of the assignment (global, tenant:X, etc.).
        assigned_by: Principal making the assignment.
    """

    principal_id: str
    role_name: str
    scope: str = "global"
    assigned_by: str = "system"


@dataclass(frozen=True)
class RevokeRoleCommand(Command):
    """Command to revoke a role from a principal.

    Attributes:
        principal_id: Principal losing the role.
        role_name: Name of the role to revoke.
        scope: Scope from which to revoke.
        revoked_by: Principal making the revocation.
    """

    principal_id: str
    role_name: str
    scope: str = "global"
    revoked_by: str = "system"


@dataclass(frozen=True)
class CreateCustomRoleCommand(Command):
    """Command to create a custom role.

    Attributes:
        role_name: Name for the new role.
        description: Human-readable description.
        permissions: Set of permission strings (format: "resource:action:id").
        created_by: Principal creating the role.
    """

    role_name: str
    description: str = ""
    permissions: frozenset[str] = field(default_factory=frozenset)
    created_by: str = "system"


@dataclass(frozen=True)
class DeleteCustomRoleCommand(Command):
    """Command to delete a custom role.

    Attributes:
        role_name: Name of the role to delete.
        deleted_by: Principal deleting the role.
    """

    role_name: str
    deleted_by: str = "system"


@dataclass(frozen=True)
class UpdateCustomRoleCommand(Command):
    """Command to update a custom role's permissions and description.

    Attributes:
        role_name: Name of the role to update.
        permissions: New set of permission strings (format: "resource:action:id").
        description: New human-readable description (None to clear).
        updated_by: Principal making the update.
    """

    role_name: str
    permissions: list[str] = field(default_factory=list)
    description: str | None = None
    updated_by: str = "system"


@dataclass(frozen=True)
class SetToolAccessPolicyCommand(Command):
    """Command to set (upsert) a tool access policy for a scope/target.

    Attributes:
        scope: One of "provider", "group", or "member".
        target_id: Identifier of the provider, group, or member.
        allow_list: Tool name patterns to allow (empty = no allow restriction).
        deny_list: Tool name patterns to deny (empty = no deny restriction).
    """

    scope: str
    target_id: str
    allow_list: list[str] = field(default_factory=list)
    deny_list: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ClearToolAccessPolicyCommand(Command):
    """Command to remove a tool access policy for a scope/target.

    Attributes:
        scope: One of "provider", "group", or "member".
        target_id: Identifier of the provider, group, or member.
    """

    scope: str
    target_id: str
