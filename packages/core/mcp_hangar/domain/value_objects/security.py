"""Security-related value objects for authentication and authorization.

Contains:
- PrincipalType, PrincipalId, Principal - identity representation
- Permission, Role - authorization primitives
"""

from dataclasses import dataclass
from enum import Enum


class PrincipalType(Enum):
    """Type of authenticated principal.

    Attributes:
        USER: Human user authenticated via JWT/OIDC or session.
        SERVICE_ACCOUNT: Non-human identity, typically authenticated via API key.
        SYSTEM: Internal system principal for background operations.
    """

    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    SYSTEM = "system"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class PrincipalId:
    """Unique identifier for an authenticated principal.

    PrincipalIds follow the format: [type:]identifier
    Examples: "user:john@example.com", "service:ci-pipeline", "system"

    Attributes:
        value: The identifier string (1-256 chars, alphanumeric + -_:@.)
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("PrincipalId cannot be empty")
        if len(self.value) > 256:
            raise ValueError("PrincipalId must be 1-256 characters")
        # Allow alphanumeric and -_:@.
        allowed_chars = set("-_:@.")
        if not all(c.isalnum() or c in allowed_chars for c in self.value):
            raise ValueError(
                f"PrincipalId contains invalid characters: {self.value!r}. Only alphanumeric and -_:@. are allowed."
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Principal:
    """Authenticated identity making a request.

    Immutable value object representing a verified identity with associated
    groups and metadata. Used throughout the authorization layer.

    Attributes:
        id: Unique identifier for the principal.
        type: Classification of the principal (user, service_account, system).
        tenant_id: Optional tenant identifier for multi-tenancy.
        groups: Immutable set of group memberships.
        metadata: Additional identity information (email, display_name, etc.).
    """

    id: PrincipalId
    type: PrincipalType
    tenant_id: str | None = None
    groups: frozenset[str] = frozenset()
    metadata: dict | None = None

    def __post_init__(self) -> None:
        # Ensure metadata is a new dict copy to maintain immutability semantics
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})
        else:
            object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def system(cls) -> "Principal":
        """Return the system principal for internal operations.

        The system principal has implicit full access and is used for
        background tasks, health checks, and internal operations.
        """
        return cls(
            id=PrincipalId("system"),
            type=PrincipalType.SYSTEM,
        )

    @classmethod
    def anonymous(cls) -> "Principal":
        """Return anonymous principal for unauthenticated requests.

        Used when authentication is optional and no credentials provided.
        """
        return cls(
            id=PrincipalId("anonymous"),
            type=PrincipalType.USER,
        )

    def is_system(self) -> bool:
        """Check if this is the system principal."""
        return self.type == PrincipalType.SYSTEM

    def is_anonymous(self) -> bool:
        """Check if this is the anonymous principal."""
        return self.id.value == "anonymous"

    def in_group(self, group: str) -> bool:
        """Check if principal is a member of the specified group."""
        return group in self.groups


@dataclass(frozen=True)
class Permission:
    """A specific permission that can be granted.

    Permissions follow the format: resource_type:action:resource_id
    Examples: "provider:read:*", "tool:invoke:math:add", "config:update:*"

    Wildcard (*) matches any value for that component.

    Attributes:
        resource_type: Type of resource (provider, tool, config, audit, metrics).
        action: Operation to perform (create, read, update, delete, invoke, list, start, stop).
        resource_id: Specific resource or wildcard (*) for any.
    """

    resource_type: str
    action: str
    resource_id: str = "*"

    def __post_init__(self) -> None:
        if not self.resource_type:
            raise ValueError("Permission resource_type cannot be empty")
        if not self.action:
            raise ValueError("Permission action cannot be empty")
        if not self.resource_id:
            raise ValueError("Permission resource_id cannot be empty")

    def matches(self, resource_type: str, action: str, resource_id: str) -> bool:
        """Check if this permission grants access to the requested operation.

        Args:
            resource_type: The type of resource being accessed.
            action: The action being performed.
            resource_id: The specific resource identifier.

        Returns:
            True if this permission grants access, False otherwise.
        """
        if self.resource_type != "*" and self.resource_type != resource_type:
            return False
        if self.action != "*" and self.action != action:
            return False
        if self.resource_id != "*" and self.resource_id != resource_id:
            return False
        return True

    def __str__(self) -> str:
        return f"{self.resource_type}:{self.action}:{self.resource_id}"

    @classmethod
    def parse(cls, permission_str: str) -> "Permission":
        """Parse permission from string format 'resource:action[:id]'.

        Args:
            permission_str: Permission string to parse.

        Returns:
            Parsed Permission object.

        Raises:
            ValueError: If the format is invalid.

        Examples:
            >>> Permission.parse("provider:read")
            Permission(resource_type='provider', action='read', resource_id='*')
            >>> Permission.parse("tool:invoke:math:add")
            Permission(resource_type='tool', action='invoke', resource_id='math:add')
        """
        parts = permission_str.split(":", 2)  # Split into at most 3 parts
        if len(parts) < 2:
            raise ValueError(
                f"Invalid permission format: {permission_str!r}. Expected 'resource:action' or 'resource:action:id'."
            )
        if len(parts) == 2:
            return cls(resource_type=parts[0], action=parts[1])
        return cls(resource_type=parts[0], action=parts[1], resource_id=parts[2])


@dataclass(frozen=True)
class Role:
    """Named collection of permissions.

    Roles group related permissions for easier assignment and management.
    Built-in roles include: admin, provider-admin, developer, viewer, auditor.

    Attributes:
        name: Unique role identifier.
        permissions: Immutable set of permissions granted by this role.
        description: Human-readable description of the role's purpose.
    """

    name: str
    permissions: frozenset[Permission]
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Role name cannot be empty")
        if not self.name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                f"Role name contains invalid characters: {self.name!r}. Only alphanumeric and -_ are allowed."
            )

    def has_permission(self, resource_type: str, action: str, resource_id: str = "*") -> bool:
        """Check if this role grants the requested permission.

        Args:
            resource_type: Type of resource being accessed.
            action: Operation being performed.
            resource_id: Specific resource or '*' for any.

        Returns:
            True if any permission in this role matches the request.
        """
        return any(p.matches(resource_type, action, resource_id) for p in self.permissions)

    def __str__(self) -> str:
        return self.name
