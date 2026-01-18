"""Authorization contracts (ports) for the domain layer.

These protocols define the interfaces for authorization components.
Infrastructure layer provides concrete implementations.
"""

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..value_objects import Permission, Principal, Role


@dataclass
class AuthorizationRequest:
    """Request to check authorization.

    Contains all information needed to make an authorization decision.

    Attributes:
        principal: The authenticated principal requesting access.
        action: The action being requested (create, read, update, delete, invoke, etc.).
        resource_type: Type of resource (provider, tool, config, audit, metrics).
        resource_id: Specific resource identifier or '*' for any.
        context: Additional context for policy evaluation (rate limits, time, etc.).
    """

    principal: Principal
    action: str
    resource_type: str
    resource_id: str
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action:
            raise ValueError("AuthorizationRequest action cannot be empty")
        if not self.resource_type:
            raise ValueError("AuthorizationRequest resource_type cannot be empty")


@dataclass
class AuthorizationResult:
    """Result of authorization check.

    Attributes:
        allowed: Whether the action is permitted.
        reason: Human-readable reason for the decision.
        matched_permission: The permission that granted access (if allowed).
        matched_role: The role that provided the permission (if allowed).
    """

    allowed: bool
    reason: str = ""
    matched_permission: Permission | None = None
    matched_role: str | None = None

    @classmethod
    def allow(
        cls,
        reason: str = "",
        permission: Permission | None = None,
        role: str | None = None,
    ) -> "AuthorizationResult":
        """Create an allow result."""
        return cls(
            allowed=True,
            reason=reason,
            matched_permission=permission,
            matched_role=role,
        )

    @classmethod
    def deny(cls, reason: str = "") -> "AuthorizationResult":
        """Create a deny result."""
        return cls(allowed=False, reason=reason)


@runtime_checkable
class IAuthorizer(Protocol):
    """Checks if a principal is authorized for an action.

    Authorizers make access control decisions based on:
    - Principal identity and attributes
    - Requested action
    - Target resource
    - Optional context (rate limits, time-based rules, etc.)
    """

    @abstractmethod
    def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        """Check if the principal is authorized.

        Args:
            request: The authorization request with principal, action, and resource.

        Returns:
            AuthorizationResult with allowed status and reason.

        Note:
            This method should never raise exceptions for authorization failures.
            Authorization denial is represented in the result, not via exceptions.
        """
        ...


@runtime_checkable
class IRoleStore(Protocol):
    """Storage for roles and role assignments.

    Handles:
    - Role definitions (name -> permissions)
    - Role assignments (principal -> roles, optionally scoped)

    Roles can be assigned globally or scoped to a tenant/namespace.
    """

    @abstractmethod
    def get_role(self, role_name: str) -> Role | None:
        """Get role by name.

        Args:
            role_name: Name of the role to retrieve.

        Returns:
            Role if found, None otherwise.
        """
        ...

    @abstractmethod
    def get_roles_for_principal(
        self,
        principal_id: str,
        scope: str = "*",
    ) -> list[Role]:
        """Get all roles assigned to a principal.

        Args:
            principal_id: ID of the principal.
            scope: Filter by scope ('*' for all, 'global', 'tenant:X', etc.).

        Returns:
            List of roles assigned to the principal.
        """
        ...

    @abstractmethod
    def assign_role(
        self,
        principal_id: str,
        role_name: str,
        scope: str = "global",
    ) -> None:
        """Assign a role to a principal.

        Args:
            principal_id: ID of the principal receiving the role.
            role_name: Name of the role to assign.
            scope: Scope of the assignment (global, tenant:X, namespace:Y).

        Raises:
            ValueError: If role_name doesn't exist.
        """
        ...

    @abstractmethod
    def revoke_role(
        self,
        principal_id: str,
        role_name: str,
        scope: str = "global",
    ) -> None:
        """Revoke a role from a principal.

        Args:
            principal_id: ID of the principal losing the role.
            role_name: Name of the role to revoke.
            scope: Scope from which to revoke (global, tenant:X, namespace:Y).
        """
        ...


@runtime_checkable
class IPolicyEngine(Protocol):
    """External policy engine (e.g., OPA) for complex authorization.

    Used when built-in RBAC is insufficient and complex policies
    are needed (multi-tenant isolation, time-based access, etc.).
    """

    @abstractmethod
    def evaluate(self, input_data: dict[str, Any]) -> AuthorizationResult:
        """Evaluate policy with given input.

        Args:
            input_data: Policy input including principal, action, resource, context.

        Returns:
            AuthorizationResult from policy evaluation.

        Note:
            Should fail closed (deny) on errors. Never raise exceptions
            that would bypass authorization.
        """
        ...

    @staticmethod
    def build_input(request: AuthorizationRequest) -> dict[str, Any]:
        """Build policy engine input from authorization request.

        Args:
            request: The authorization request.

        Returns:
            Dictionary formatted for policy engine input.
        """
        return {
            "principal": {
                "id": request.principal.id.value,
                "type": request.principal.type.value,
                "tenant_id": request.principal.tenant_id,
                "groups": list(request.principal.groups),
            },
            "action": request.action,
            "resource": {
                "type": request.resource_type,
                "id": request.resource_id,
            },
            "context": request.context,
        }
