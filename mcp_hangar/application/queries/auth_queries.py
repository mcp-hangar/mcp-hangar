"""Authentication and Authorization queries.

Queries represent read operations in CQRS pattern.
They do not modify state, only retrieve data.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    """Base class for all queries."""

    pass


# =============================================================================
# API Key Queries
# =============================================================================


@dataclass(frozen=True)
class GetApiKeysByPrincipalQuery(Query):
    """Query to get all API keys for a principal.

    Attributes:
        principal_id: Principal whose keys to retrieve.
        include_revoked: Whether to include revoked keys.
    """

    principal_id: str
    include_revoked: bool = True


@dataclass(frozen=True)
class GetApiKeyCountQuery(Query):
    """Query to get count of active API keys for a principal.

    Attributes:
        principal_id: Principal whose keys to count.
    """

    principal_id: str


# =============================================================================
# Role Queries
# =============================================================================


@dataclass(frozen=True)
class GetRolesForPrincipalQuery(Query):
    """Query to get all roles assigned to a principal.

    Attributes:
        principal_id: Principal whose roles to retrieve.
        scope: Optional scope filter (use "*" for all scopes).
    """

    principal_id: str
    scope: str = "*"


@dataclass(frozen=True)
class GetRoleQuery(Query):
    """Query to get a specific role by name.

    Attributes:
        role_name: Name of the role to retrieve.
    """

    role_name: str


@dataclass(frozen=True)
class ListBuiltinRolesQuery(Query):
    """Query to list all built-in roles."""

    pass


@dataclass(frozen=True)
class CheckPermissionQuery(Query):
    """Query to check if a principal has a specific permission.

    Attributes:
        principal_id: Principal to check.
        action: Action being requested.
        resource_type: Type of resource.
        resource_id: Specific resource ID.
    """

    principal_id: str
    action: str
    resource_type: str
    resource_id: str = "*"


# =============================================================================
# Audit Queries
# =============================================================================


@dataclass(frozen=True)
class GetAuthAuditLogQuery(Query):
    """Query to get authentication audit log entries.

    Attributes:
        principal_id: Optional filter by principal.
        event_type: Optional filter by event type.
        limit: Maximum number of entries.
        since_timestamp: Optional filter for entries after this time.
    """

    principal_id: str | None = None
    event_type: str | None = None
    limit: int = 100
    since_timestamp: float | None = None
