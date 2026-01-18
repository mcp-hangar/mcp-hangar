"""RBAC (Role-Based Access Control) authorization implementation.

Provides authorizer and in-memory role store for role-based access control.
"""

import threading

import structlog

from ...domain.contracts.authorization import AuthorizationRequest, AuthorizationResult, IAuthorizer, IRoleStore
from ...domain.security.roles import BUILTIN_ROLES
from ...domain.value_objects import Permission, Principal, Role

logger = structlog.get_logger(__name__)


class RBACAuthorizer(IAuthorizer):
    """Role-Based Access Control authorizer.

    Checks authorization based on:
    1. System principal - always allowed
    2. Direct role assignments to principal
    3. Role assignments to principal's groups

    Multiple scopes are supported (global, tenant, namespace).
    """

    def __init__(self, role_store: IRoleStore):
        """Initialize the RBAC authorizer.

        Args:
            role_store: Storage backend for roles and assignments.
        """
        self._role_store = role_store

    def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        """Check if principal is authorized for the action.

        Args:
            request: Authorization request with principal, action, and resource.

        Returns:
            AuthorizationResult with allowed status and reason.
        """
        principal = request.principal

        # System principal has full access
        if principal.is_system():
            logger.debug(
                "authorization_granted_system",
                action=request.action,
                resource_type=request.resource_type,
                resource_id=request.resource_id,
            )
            return AuthorizationResult.allow(reason="system_principal")

        # Collect all roles for principal
        roles = self._collect_roles(principal)

        # Check each role for matching permission
        for role in roles:
            if role.has_permission(request.resource_type, request.action, request.resource_id):
                # Find the specific permission that matched (for audit)
                matched_permission = self._find_matching_permission(
                    role, request.resource_type, request.action, request.resource_id
                )

                logger.debug(
                    "authorization_granted",
                    principal_id=principal.id.value,
                    role=role.name,
                    action=request.action,
                    resource_type=request.resource_type,
                    resource_id=request.resource_id,
                )

                return AuthorizationResult.allow(
                    reason=f"granted_by_role:{role.name}",
                    permission=matched_permission,
                    role=role.name,
                )

        # No matching permission found
        logger.warning(
            "authorization_denied",
            principal_id=principal.id.value,
            action=request.action,
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            roles_checked=[r.name for r in roles],
        )

        return AuthorizationResult.deny(reason="no_matching_permission")

    def _collect_roles(self, principal: Principal) -> list[Role]:
        """Collect all roles for a principal.

        Includes:
        - Direct role assignments (global scope)
        - Group-based role assignments (global scope)
        - Tenant-scoped assignments if principal has tenant_id

        Args:
            principal: The principal to collect roles for.

        Returns:
            List of all applicable roles.
        """
        roles: list[Role] = []

        # Direct assignments - global scope only
        direct_roles = self._role_store.get_roles_for_principal(principal.id.value, scope="global")
        roles.extend(direct_roles)

        # Group-based assignments - global scope only
        for group in principal.groups:
            group_roles = self._role_store.get_roles_for_principal(f"group:{group}", scope="global")
            roles.extend(group_roles)

        # Tenant-scoped assignments (only if principal has tenant_id)
        if principal.tenant_id:
            tenant_scope = f"tenant:{principal.tenant_id}"
            # Direct tenant-scoped roles
            tenant_roles = self._role_store.get_roles_for_principal(principal.id.value, scope=tenant_scope)
            roles.extend(tenant_roles)
            # Group-based tenant-scoped roles
            for group in principal.groups:
                group_tenant_roles = self._role_store.get_roles_for_principal(f"group:{group}", scope=tenant_scope)
                roles.extend(group_tenant_roles)

        return roles

    def _find_matching_permission(
        self,
        role: Role,
        resource_type: str,
        action: str,
        resource_id: str,
    ) -> Permission | None:
        """Find the specific permission that matched.

        Args:
            role: Role to search.
            resource_type: Resource type to match.
            action: Action to match.
            resource_id: Resource ID to match.

        Returns:
            The matching Permission, or None if not found.
        """
        for permission in role.permissions:
            if permission.matches(resource_type, action, resource_id):
                return permission
        return None


class InMemoryRoleStore(IRoleStore):
    """In-memory role store for development/testing.

    WARNING: Data is lost on restart. Use a persistent store
    for production.

    This implementation is thread-safe using a reentrant lock.
    """

    def __init__(self) -> None:
        """Initialize with built-in roles."""
        self._lock = threading.RLock()
        # role_name -> Role
        self._roles: dict[str, Role] = dict(BUILTIN_ROLES)
        # principal_id -> scope -> set of role names
        self._assignments: dict[str, dict[str, set[str]]] = {}

    def get_role(self, role_name: str) -> Role | None:
        """Get role by name.

        Args:
            role_name: Name of the role to retrieve.

        Returns:
            Role if found, None otherwise.
        """
        with self._lock:
            return self._roles.get(role_name)

    def add_role(self, role: Role) -> None:
        """Add a custom role.

        Args:
            role: Role to add.
        """
        with self._lock:
            self._roles[role.name] = role
            logger.info("role_added", role_name=role.name)

    def get_roles_for_principal(
        self,
        principal_id: str,
        scope: str = "*",
    ) -> list[Role]:
        """Get all roles assigned to a principal.

        Args:
            principal_id: ID of the principal.
            scope: Filter by scope ('*' for all, 'global' for global only,
                   'tenant:X' for that specific scope only, etc.).

        Returns:
            List of roles assigned to the principal for the specified scope.

        Note:
            When scope is specific (not '*'), only roles in that exact scope
            are returned. The caller is responsible for aggregating roles
            from multiple scopes if needed.
        """
        with self._lock:
            if principal_id not in self._assignments:
                return []

            principal_assignments = self._assignments[principal_id]
            role_names: set[str] = set()

            if scope == "*":
                # All scopes
                for scope_roles in principal_assignments.values():
                    role_names.update(scope_roles)
            else:
                # Specific scope only - no automatic global inclusion
                role_names.update(principal_assignments.get(scope, set()))

            # Convert role names to Role objects
            return [self._roles[name] for name in role_names if name in self._roles]

    def assign_role(
        self,
        principal_id: str,
        role_name: str,
        scope: str = "global",
        assigned_by: str = "system",
    ) -> None:
        """Assign a role to a principal.

        Args:
            principal_id: ID of the principal receiving the role.
            role_name: Name of the role to assign.
            scope: Scope of the assignment.
            assigned_by: Principal making the assignment.

        Raises:
            ValueError: If role_name doesn't exist.
        """
        with self._lock:
            if role_name not in self._roles:
                raise ValueError(f"Unknown role: {role_name}")

            if principal_id not in self._assignments:
                self._assignments[principal_id] = {}

            if scope not in self._assignments[principal_id]:
                self._assignments[principal_id][scope] = set()

            self._assignments[principal_id][scope].add(role_name)

            logger.info(
                "role_assigned",
                principal_id=principal_id,
                role_name=role_name,
                scope=scope,
                assigned_by=assigned_by,
            )

    def revoke_role(
        self,
        principal_id: str,
        role_name: str,
        scope: str = "global",
        revoked_by: str = "system",
    ) -> None:
        """Revoke a role from a principal.

        Args:
            principal_id: ID of the principal losing the role.
            role_name: Name of the role to revoke.
            scope: Scope from which to revoke.
            revoked_by: Principal making the revocation.
        """
        with self._lock:
            if principal_id in self._assignments:
                if scope in self._assignments[principal_id]:
                    self._assignments[principal_id][scope].discard(role_name)
                    logger.info(
                        "role_revoked",
                        principal_id=principal_id,
                        role_name=role_name,
                        scope=scope,
                        revoked_by=revoked_by,
                    )

    def list_assignments(self, principal_id: str) -> dict[str, list[str]]:
        """List all role assignments for a principal.

        Args:
            principal_id: ID of the principal.

        Returns:
            Dict of scope -> list of role names.
        """
        with self._lock:
            if principal_id not in self._assignments:
                return {}

            return {scope: list(roles) for scope, roles in self._assignments[principal_id].items()}

    def clear_assignments(self, principal_id: str) -> None:
        """Clear all role assignments for a principal.

        Args:
            principal_id: ID of the principal.
        """
        with self._lock:
            if principal_id in self._assignments:
                del self._assignments[principal_id]
                logger.info("assignments_cleared", principal_id=principal_id)
