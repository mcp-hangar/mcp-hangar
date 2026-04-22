"""Authorization contracts (ports) for the domain layer.

These protocols define the interfaces for authorization components.
Infrastructure layer provides concrete implementations.
"""

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..value_objects import Permission, Principal, Role
from ..value_objects.tool_access_policy import ToolAccessPolicy


@dataclass
class AuthorizationRequest:
    """Request to check authorization.

    Contains all information needed to make an authorization decision.

    Attributes:
        principal: The authenticated principal requesting access.
        action: The action being requested (create, read, update, delete, invoke, etc.).
        resource_type: Type of resource (mcp_server, tool, config, audit, metrics).
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

    @abstractmethod
    def list_all_roles(self) -> list[Role]:
        """List all custom (non-builtin) roles.

        Returns:
            List of all custom roles in the store.
        """
        ...

    @abstractmethod
    def delete_role(self, role_name: str) -> None:
        """Delete a custom role and remove all its assignments.

        Args:
            role_name: Name of the role to delete.

        Raises:
            RoleNotFoundError: If the role does not exist.
            CannotModifyBuiltinRoleError: If the role is a built-in role.
        """
        ...

    @abstractmethod
    def update_role(
        self,
        role_name: str,
        permissions: list["Permission"],
        description: str | None,
    ) -> Role:
        """Update a custom role's permissions and description.

        Args:
            role_name: Name of the role to update.
            permissions: New list of permissions.
            description: New description (None to clear).

        Returns:
            Updated Role value object.

        Raises:
            RoleNotFoundError: If the role does not exist.
            CannotModifyBuiltinRoleError: If the role is a built-in role.
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


@runtime_checkable
class IToolAccessPolicyStore(Protocol):
    """Persistent storage for tool access policies.

    Stores per-scope tool access policies that survive server restarts.
    Scope values: "mcp_server", "group", "member".
    """

    @abstractmethod
    def set_policy(
        self,
        scope: str,
        target_id: str,
        allow_list: list[str],
        deny_list: list[str],
    ) -> None:
        """Persist a tool access policy for a scope/target combination.

        Args:
            scope: "mcp_server", "group", or "member".
            target_id: Identifier of the mcp_server, group, or member.
            allow_list: Tool name patterns to allow.
            deny_list: Tool name patterns to deny.
        """
        ...

    @abstractmethod
    def get_policy(self, scope: str, target_id: str) -> ToolAccessPolicy | None:
        """Retrieve a stored policy.

        Args:
            scope: Scope string.
            target_id: Target identifier.

        Returns:
            ToolAccessPolicy if found, None otherwise.
        """
        ...

    @abstractmethod
    def clear_policy(self, scope: str, target_id: str) -> None:
        """Remove a stored policy.

        Args:
            scope: Scope string.
            target_id: Target identifier.
        """
        ...

    @abstractmethod
    def list_all_policies(self) -> list[tuple[str, str, list[str], list[str]]]:
        """List all stored policies for startup replay.

        Returns:
            List of (scope, target_id, allow_list, deny_list) tuples.
        """
        ...


@dataclass
class PolicyEvaluationResult:
    """Result of a tool access policy evaluation.

    Attributes:
        allowed: Whether the tool invocation is permitted.
        reason: Human-readable explanation of the decision.
        policy_id: Identifier of the policy that made the decision (for audit).
    """

    allowed: bool
    reason: str = ""
    policy_id: str | None = None

    @classmethod
    def allow(cls, reason: str = "", policy_id: str | None = None) -> "PolicyEvaluationResult":
        """Create an allow result."""
        return cls(allowed=True, reason=reason, policy_id=policy_id)

    @classmethod
    def deny(cls, reason: str = "", policy_id: str | None = None) -> "PolicyEvaluationResult":
        """Create a deny result."""
        return cls(allowed=False, reason=reason, policy_id=policy_id)


@runtime_checkable
class IToolAccessPolicyEnforcer(Protocol):
    """Runtime enforcement of tool access policies.

    Evaluates whether a principal can invoke a specific tool on a mcp_server,
    considering all applicable policies (mcp_server-level, group-level, member-level).

    This is the enforcement contract -- distinct from IToolAccessPolicyStore which
    handles policy storage/retrieval. Enterprise RBAC implements this with
    identity-aware policy resolution. Core provides a config-driven implementation
    using ToolAccessPolicy value objects.
    """

    @abstractmethod
    def evaluate(
        self,
        principal: Principal,
        mcp_server_id: str,
        tool_name: str,
        context: dict[str, Any] | None = None,
    ) -> PolicyEvaluationResult:
        """Evaluate whether a tool invocation is allowed.

        Args:
            principal: The authenticated principal requesting access.
            mcp_server_id: ID of the mcp_server owning the tool.
            tool_name: Name of the tool being invoked.
            context: Optional additional context (group membership, etc.).

        Returns:
            PolicyEvaluationResult with decision and reason.
        """
        ...


class NullAuthorizer:
    """No-op authorizer. Allows all requests.

    Used when enterprise RBAC is not installed or during testing.
    """

    def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        """Allow all requests when no RBAC is configured."""
        return AuthorizationResult.allow(reason="No RBAC configured (null authorizer)")


class NullRoleStore:
    """No-op role store. Returns empty results for all queries.

    Used when enterprise RBAC is not installed or during testing.
    """

    def get_role(self, role_name: str) -> Role | None:
        """No roles defined."""
        return None

    def get_roles_for_principal(
        self,
        principal_id: str,
        scope: str = "*",
    ) -> list[Role]:
        """No roles assigned."""
        return []

    def assign_role(
        self,
        principal_id: str,
        role_name: str,
        scope: str = "global",
    ) -> None:
        """No-op: role assignment requires enterprise RBAC."""

    def revoke_role(
        self,
        principal_id: str,
        role_name: str,
        scope: str = "global",
    ) -> None:
        """No-op: role revocation requires enterprise RBAC."""

    def list_all_roles(self) -> list[Role]:
        """No custom roles defined."""
        return []

    def delete_role(self, role_name: str) -> None:
        """No-op: role deletion requires enterprise RBAC."""

    def update_role(
        self,
        role_name: str,
        permissions: list[Permission],
        description: str | None = None,
    ) -> Role:
        """No-op: raise NotImplementedError (no role management without enterprise)."""
        raise NotImplementedError("Role management requires enterprise RBAC module")


class NullToolAccessPolicyStore:
    """No-op tool access policy store. Returns None for all lookups.

    Used when enterprise policy storage is not installed or during testing.
    """

    def set_policy(
        self,
        scope: str,
        target_id: str,
        allow_list: list[str],
        deny_list: list[str],
    ) -> None:
        """No-op: policy storage requires enterprise module."""

    def get_policy(self, scope: str, target_id: str) -> ToolAccessPolicy | None:
        """No policies stored."""
        return None

    def clear_policy(self, scope: str, target_id: str) -> None:
        """No-op."""

    def list_all_policies(self) -> list[tuple[str, str, list[str], list[str]]]:
        """No policies stored."""
        return []


class NullToolAccessPolicyEnforcer:
    """No-op policy enforcer. Allows all tool invocations.

    Used when enterprise policy enforcement is not installed or during testing.
    """

    def evaluate(
        self,
        principal: Principal,
        tool_name: str,
        mcp_server_id: str | None = None,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> PolicyEvaluationResult:
        """Allow all tool invocations when no policy enforcement is configured."""
        return PolicyEvaluationResult.allow(reason="No policy enforcement configured (null enforcer)")
