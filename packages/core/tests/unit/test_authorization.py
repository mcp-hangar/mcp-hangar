"""Unit tests for authorization components.

Tests cover:
- RBAC authorizer
- Role store
- Authorization middleware
- Built-in roles
"""

import pytest

from mcp_hangar.domain.contracts.authorization import AuthorizationRequest
from mcp_hangar.domain.exceptions import AccessDeniedError
from mcp_hangar.domain.security.roles import get_builtin_role, list_builtin_roles
from mcp_hangar.domain.value_objects import Permission, Principal, PrincipalId, PrincipalType, Role
from mcp_hangar.infrastructure.auth.middleware import AuthorizationMiddleware
from mcp_hangar.infrastructure.auth.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer


class TestBuiltinRoles:
    """Tests for built-in roles."""

    def test_admin_role_has_all_access(self):
        """Admin role has wildcard permission."""
        admin = get_builtin_role("admin")
        assert admin is not None
        assert admin.has_permission("provider", "delete", "*")
        assert admin.has_permission("tool", "invoke", "*")
        assert admin.has_permission("config", "update", "*")
        assert admin.has_permission("anything", "any_action", "any_resource")

    def test_developer_role_permissions(self):
        """Developer role has expected permissions."""
        developer = get_builtin_role("developer")
        assert developer is not None
        # Can invoke tools
        assert developer.has_permission("tool", "invoke", "*")
        assert developer.has_permission("tool", "list", "*")
        # Can read providers
        assert developer.has_permission("provider", "read", "*")
        assert developer.has_permission("provider", "list", "*")
        # Can start providers
        assert developer.has_permission("provider", "start", "*")
        # Cannot delete providers
        assert not developer.has_permission("provider", "delete", "*")

    def test_viewer_role_read_only(self):
        """Viewer role is read-only."""
        viewer = get_builtin_role("viewer")
        assert viewer is not None
        assert viewer.has_permission("provider", "read", "*")
        assert viewer.has_permission("provider", "list", "*")
        assert viewer.has_permission("tool", "list", "*")
        assert viewer.has_permission("metrics", "read", "*")
        # Cannot invoke or modify
        assert not viewer.has_permission("tool", "invoke", "*")
        assert not viewer.has_permission("provider", "delete", "*")

    def test_list_builtin_roles(self):
        """All built-in roles are listed."""
        roles = list_builtin_roles()
        assert "admin" in roles
        assert "provider-admin" in roles
        assert "developer" in roles
        assert "viewer" in roles
        assert "auditor" in roles

    def test_unknown_role_returns_none(self):
        """Unknown role returns None."""
        role = get_builtin_role("nonexistent")
        assert role is None


class TestRBACAuthorizer:
    """Tests for RBAC authorizer."""

    def test_system_principal_always_allowed(self):
        """System principal has full access."""
        store = InMemoryRoleStore()
        authorizer = RBACAuthorizer(store)

        request = AuthorizationRequest(
            principal=Principal.system(),
            action="delete",
            resource_type="provider",
            resource_id="any-provider",
        )

        result = authorizer.authorize(request)
        assert result.allowed
        assert result.reason == "system_principal"

    def test_admin_role_allows_all(self):
        """Admin role grants access to all resources."""
        store = InMemoryRoleStore()
        store.assign_role("user:admin", "admin")

        authorizer = RBACAuthorizer(store)

        principal = Principal(
            id=PrincipalId("user:admin"),
            type=PrincipalType.USER,
        )
        request = AuthorizationRequest(
            principal=principal,
            action="delete",
            resource_type="provider",
            resource_id="any-provider",
        )

        result = authorizer.authorize(request)
        assert result.allowed
        assert "admin" in result.reason

    def test_developer_cannot_delete_provider(self):
        """Developer role denies provider:delete."""
        store = InMemoryRoleStore()
        store.assign_role("user:dev", "developer")

        authorizer = RBACAuthorizer(store)

        principal = Principal(
            id=PrincipalId("user:dev"),
            type=PrincipalType.USER,
        )
        request = AuthorizationRequest(
            principal=principal,
            action="delete",
            resource_type="provider",
            resource_id="my-provider",
        )

        result = authorizer.authorize(request)
        assert not result.allowed

    def test_developer_can_invoke_tool(self):
        """Developer role allows tool invocation."""
        store = InMemoryRoleStore()
        store.assign_role("user:dev", "developer")

        authorizer = RBACAuthorizer(store)

        principal = Principal(
            id=PrincipalId("user:dev"),
            type=PrincipalType.USER,
        )
        request = AuthorizationRequest(
            principal=principal,
            action="invoke",
            resource_type="tool",
            resource_id="math:add",
        )

        result = authorizer.authorize(request)
        assert result.allowed

    def test_group_membership_grants_role(self):
        """Principal in group inherits group's roles."""
        store = InMemoryRoleStore()
        store.assign_role("group:developers", "developer")

        authorizer = RBACAuthorizer(store)

        principal = Principal(
            id=PrincipalId("user:john"),
            type=PrincipalType.USER,
            groups=frozenset(["developers"]),
        )
        request = AuthorizationRequest(
            principal=principal,
            action="invoke",
            resource_type="tool",
            resource_id="math:add",
        )

        result = authorizer.authorize(request)
        assert result.allowed

    def test_no_role_denies_access(self):
        """Principal without roles is denied access."""
        store = InMemoryRoleStore()
        authorizer = RBACAuthorizer(store)

        principal = Principal(
            id=PrincipalId("user:nobody"),
            type=PrincipalType.USER,
        )
        request = AuthorizationRequest(
            principal=principal,
            action="read",
            resource_type="provider",
            resource_id="my-provider",
        )

        result = authorizer.authorize(request)
        assert not result.allowed
        assert result.reason == "no_matching_permission"

    def test_scoped_role_respects_scope(self):
        """Tenant-scoped role only works in that tenant."""
        store = InMemoryRoleStore()
        store.assign_role("user:dev", "developer", scope="tenant:acme")

        authorizer = RBACAuthorizer(store)

        # Principal with matching tenant
        principal_acme = Principal(
            id=PrincipalId("user:dev"),
            type=PrincipalType.USER,
            tenant_id="acme",
        )
        request = AuthorizationRequest(
            principal=principal_acme,
            action="invoke",
            resource_type="tool",
            resource_id="math:add",
        )

        result = authorizer.authorize(request)
        assert result.allowed

        # Principal without tenant
        principal_no_tenant = Principal(
            id=PrincipalId("user:dev"),
            type=PrincipalType.USER,
        )
        request2 = AuthorizationRequest(
            principal=principal_no_tenant,
            action="invoke",
            resource_type="tool",
            resource_id="math:add",
        )

        result2 = authorizer.authorize(request2)
        assert not result2.allowed


class TestInMemoryRoleStore:
    """Tests for in-memory role store."""

    def test_builtin_roles_available(self):
        """Built-in roles are available on initialization."""
        store = InMemoryRoleStore()

        admin = store.get_role("admin")
        assert admin is not None
        assert admin.name == "admin"

    def test_assign_and_get_role(self):
        """Assign role and retrieve it."""
        store = InMemoryRoleStore()
        store.assign_role("user:test", "developer")

        roles = store.get_roles_for_principal("user:test")
        assert len(roles) == 1
        assert roles[0].name == "developer"

    def test_assign_multiple_roles(self):
        """Multiple roles can be assigned."""
        store = InMemoryRoleStore()
        store.assign_role("user:test", "developer")
        store.assign_role("user:test", "viewer")

        roles = store.get_roles_for_principal("user:test")
        assert len(roles) == 2
        role_names = {r.name for r in roles}
        assert role_names == {"developer", "viewer"}

    def test_revoke_role(self):
        """Revoke role removes it."""
        store = InMemoryRoleStore()
        store.assign_role("user:test", "developer")
        store.revoke_role("user:test", "developer")

        roles = store.get_roles_for_principal("user:test")
        assert len(roles) == 0

    def test_assign_unknown_role_raises(self):
        """Assigning unknown role raises ValueError."""
        store = InMemoryRoleStore()

        with pytest.raises(ValueError, match="Unknown role"):
            store.assign_role("user:test", "nonexistent")

    def test_add_custom_role(self):
        """Custom role can be added."""
        store = InMemoryRoleStore()

        custom_role = Role(
            name="custom-role",
            permissions=frozenset([Permission("custom", "action")]),
            description="Custom role",
        )
        store.add_role(custom_role)

        role = store.get_role("custom-role")
        assert role is not None
        assert role.name == "custom-role"

    def test_list_assignments(self):
        """List all assignments for a principal."""
        store = InMemoryRoleStore()
        store.assign_role("user:test", "developer", scope="global")
        store.assign_role("user:test", "admin", scope="tenant:acme")

        assignments = store.list_assignments("user:test")
        assert "global" in assignments
        assert "tenant:acme" in assignments
        assert "developer" in assignments["global"]
        assert "admin" in assignments["tenant:acme"]

    def test_clear_assignments(self):
        """Clear all assignments for a principal."""
        store = InMemoryRoleStore()
        store.assign_role("user:test", "developer")
        store.assign_role("user:test", "viewer")

        store.clear_assignments("user:test")

        roles = store.get_roles_for_principal("user:test")
        assert len(roles) == 0


class TestAuthorizationMiddleware:
    """Tests for authorization middleware."""

    def test_authorize_success(self):
        """Successful authorization does not raise."""
        store = InMemoryRoleStore()
        store.assign_role("user:dev", "developer")
        authorizer = RBACAuthorizer(store)
        middleware = AuthorizationMiddleware(authorizer)

        principal = Principal(
            id=PrincipalId("user:dev"),
            type=PrincipalType.USER,
        )

        # Should not raise
        middleware.authorize(
            principal=principal,
            action="invoke",
            resource_type="tool",
            resource_id="math:add",
        )

    def test_authorize_failure_raises(self):
        """Failed authorization raises AccessDeniedError."""
        store = InMemoryRoleStore()
        authorizer = RBACAuthorizer(store)
        middleware = AuthorizationMiddleware(authorizer)

        principal = Principal(
            id=PrincipalId("user:nobody"),
            type=PrincipalType.USER,
        )

        with pytest.raises(AccessDeniedError) as exc_info:
            middleware.authorize(
                principal=principal,
                action="delete",
                resource_type="provider",
                resource_id="my-provider",
            )

        assert exc_info.value.principal_id == "user:nobody"
        assert exc_info.value.action == "delete"
        assert "provider:my-provider" in exc_info.value.resource

    def test_check_returns_bool(self):
        """check() returns boolean without raising."""
        store = InMemoryRoleStore()
        authorizer = RBACAuthorizer(store)
        middleware = AuthorizationMiddleware(authorizer)

        principal = Principal(
            id=PrincipalId("user:nobody"),
            type=PrincipalType.USER,
        )

        result = middleware.check(
            principal=principal,
            action="delete",
            resource_type="provider",
            resource_id="my-provider",
        )

        assert result is False
