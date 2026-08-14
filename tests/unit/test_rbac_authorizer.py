"""`RBACAuthorizer` and the in-memory role store behind it."""

from unittest.mock import Mock

import pytest

from mcp_hangar.auth.infrastructure.rbac_authorizer import (
    InMemoryRoleStore,
    RBACAuthorizer,
)
from mcp_hangar.domain.contracts.authorization import AuthorizationRequest, IRoleStore
from mcp_hangar.domain.exceptions import (
    CannotModifyBuiltinRoleError,
    RoleNotFoundError,
)
from mcp_hangar.domain.value_objects import Permission, Principal, PrincipalId, PrincipalType, Role


class TestRBACAuthorizer:
    """Tests for RBACAuthorizer class."""

    def _make_authorizer(self, role_store: IRoleStore | None = None):
        store = role_store or Mock(spec=IRoleStore)
        return RBACAuthorizer(role_store=store), store

    def _make_principal(
        self,
        pid: str = "user-1",
        ptype: PrincipalType = PrincipalType.USER,
        groups: frozenset[str] = frozenset(),
        tenant_id: str | None = None,
    ):
        return Principal(
            id=PrincipalId(pid),
            type=ptype,
            groups=groups,
            tenant_id=tenant_id,
        )

    def test_system_principal_always_allowed(self):
        """Lines 45-55: system principal bypasses RBAC."""
        auth, store = self._make_authorizer()
        principal = self._make_principal(pid="system", ptype=PrincipalType.SYSTEM)
        request = AuthorizationRequest(
            principal=principal,
            action="delete",
            resource_type="provider",
            resource_id="*",
        )
        result = auth.authorize(request)
        assert result.allowed is True
        assert result.reason == "system_principal"
        # Role store should NOT be called for system principal
        store.get_roles_for_principal.assert_not_called()

    def test_authorize_granted_by_direct_role(self):
        """Lines 58-81: authorization granted through a direct role."""
        perm = Permission(resource_type="provider", action="read", resource_id="*")
        role = Role(name="viewer", permissions=frozenset([perm]), description="Viewer")
        store = Mock(spec=IRoleStore)
        store.get_roles_for_principal.return_value = [role]
        auth = RBACAuthorizer(role_store=store)

        principal = self._make_principal()
        request = AuthorizationRequest(
            principal=principal,
            action="read",
            resource_type="provider",
            resource_id="my-provider",
        )
        result = auth.authorize(request)
        assert result.allowed is True
        assert "granted_by_role:viewer" in result.reason
        assert result.matched_role == "viewer"
        assert result.matched_permission == perm

    def test_authorize_denied_no_matching_permission(self):
        """Lines 83-93: no matching permission returns deny."""
        store = Mock(spec=IRoleStore)
        store.get_roles_for_principal.return_value = []
        auth = RBACAuthorizer(role_store=store)

        principal = self._make_principal()
        request = AuthorizationRequest(
            principal=principal,
            action="delete",
            resource_type="provider",
            resource_id="*",
        )
        result = auth.authorize(request)
        assert result.allowed is False
        assert result.reason == "no_matching_permission"

    def test_collect_roles_includes_group_roles(self):
        """Lines 109-131: _collect_roles includes group-based assignments."""
        perm = Permission(resource_type="tool", action="invoke", resource_id="*")
        role = Role(name="invoker", permissions=frozenset([perm]))
        store = Mock(spec=IRoleStore)

        def get_roles(principal_id: str, scope: str = "*") -> list[Role]:
            if principal_id == "group:ops":
                return [role]
            return []

        store.get_roles_for_principal.side_effect = get_roles
        auth = RBACAuthorizer(role_store=store)

        principal = self._make_principal(groups=frozenset({"ops"}))
        request = AuthorizationRequest(
            principal=principal,
            action="invoke",
            resource_type="tool",
            resource_id="math:add",
        )
        result = auth.authorize(request)
        assert result.allowed is True

    def test_collect_roles_includes_tenant_scoped_roles(self):
        """Lines 121-129: _collect_roles includes tenant-scoped assignments."""
        perm = Permission(resource_type="config", action="update", resource_id="*")
        role = Role(name="config-manager", permissions=frozenset([perm]))
        store = Mock(spec=IRoleStore)

        def get_roles(principal_id: str, scope: str = "*") -> list[Role]:
            if scope == "tenant:x" and principal_id == "user-1":
                return [role]
            return []

        store.get_roles_for_principal.side_effect = get_roles
        auth = RBACAuthorizer(role_store=store)

        principal = self._make_principal(tenant_id="x")
        request = AuthorizationRequest(
            principal=principal,
            action="update",
            resource_type="config",
            resource_id="settings",
        )
        result = auth.authorize(request)
        assert result.allowed is True

    def test_collect_roles_tenant_scoped_group_roles(self):
        """Lines 127-129: group roles at tenant scope."""
        perm = Permission(resource_type="audit", action="read", resource_id="*")
        role = Role(name="tenant-auditor", permissions=frozenset([perm]))
        store = Mock(spec=IRoleStore)

        def get_roles(principal_id: str, scope: str = "*") -> list[Role]:
            if scope == "tenant:x" and principal_id == "group:auditors":
                return [role]
            return []

        store.get_roles_for_principal.side_effect = get_roles
        auth = RBACAuthorizer(role_store=store)

        principal = self._make_principal(groups=frozenset({"auditors"}), tenant_id="x")
        request = AuthorizationRequest(
            principal=principal,
            action="read",
            resource_type="audit",
            resource_id="*",
        )
        result = auth.authorize(request)
        assert result.allowed is True

    def test_find_matching_permission_returns_none_when_no_match(self):
        """Lines 151-154: _find_matching_permission returns None."""
        auth, _ = self._make_authorizer()
        perm = Permission(resource_type="provider", action="read", resource_id="*")
        role = Role(name="viewer", permissions=frozenset([perm]))
        result = auth._find_matching_permission(role, "tool", "invoke", "math:add")
        assert result is None

    def test_find_matching_permission_returns_matching(self):
        """Lines 151-153: _find_matching_permission returns the Permission."""
        auth, _ = self._make_authorizer()
        perm = Permission(resource_type="tool", action="invoke", resource_id="*")
        role = Role(name="invoker", permissions=frozenset([perm]))
        result = auth._find_matching_permission(role, "tool", "invoke", "math:add")
        assert result == perm


class TestInMemoryRoleStore:
    """Tests for InMemoryRoleStore class."""

    def test_init_has_builtin_roles(self):
        store = InMemoryRoleStore()
        admin_role = store.get_role("admin")
        assert admin_role is not None
        assert admin_role.name == "admin"

    def test_add_role_and_get_role(self):
        """Lines 192-194: add custom role."""
        store = InMemoryRoleStore()
        perm = Permission(resource_type="custom", action="do", resource_id="*")
        custom_role = Role(name="custom-role", permissions=frozenset([perm]))
        store.add_role(custom_role)
        retrieved = store.get_role("custom-role")
        assert retrieved is not None
        assert retrieved.name == "custom-role"

    def test_get_role_returns_none_for_unknown(self):
        store = InMemoryRoleStore()
        assert store.get_role("nonexistent") is None

    def test_get_roles_for_principal_no_assignments(self):
        """Line 218: principal not in assignments returns empty list."""
        store = InMemoryRoleStore()
        roles = store.get_roles_for_principal("user-1", scope="global")
        assert roles == []

    def test_get_roles_for_principal_specific_scope(self):
        """Line 229: specific scope returns only roles in that scope."""
        store = InMemoryRoleStore()
        store.assign_role("user-1", "admin", scope="global")
        store.assign_role("user-1", "viewer", scope="tenant:x")
        roles = store.get_roles_for_principal("user-1", scope="global")
        assert len(roles) == 1
        assert roles[0].name == "admin"

    def test_get_roles_for_principal_wildcard_scope(self):
        """Lines 223-226: scope='*' returns all scopes."""
        store = InMemoryRoleStore()
        store.assign_role("user-1", "admin", scope="global")
        store.assign_role("user-1", "viewer", scope="tenant:x")
        roles = store.get_roles_for_principal("user-1", scope="*")
        assert len(roles) == 2
        role_names = {r.name for r in roles}
        assert role_names == {"admin", "viewer"}

    def test_assign_role_unknown_role_raises(self):
        """Line 254: assigning unknown role raises ValueError."""
        store = InMemoryRoleStore()
        with pytest.raises(ValueError, match="Unknown role"):
            store.assign_role("user-1", "nonexistent-role")

    def test_revoke_role(self):
        """Lines 289-291: revoke_role removes role from assignments."""
        store = InMemoryRoleStore()
        store.assign_role("user-1", "admin")
        store.revoke_role("user-1", "admin")
        roles = store.get_roles_for_principal("user-1", scope="global")
        assert len(roles) == 0

    def test_revoke_role_nonexistent_principal_no_error(self):
        store = InMemoryRoleStore()
        # Should not raise
        store.revoke_role("nonexistent-user", "admin")

    def test_list_all_roles_excludes_builtin(self):
        """Lines 301-304: list_all_roles returns only custom roles."""
        store = InMemoryRoleStore()
        perm = Permission(resource_type="custom", action="do", resource_id="*")
        custom = Role(name="custom-role", permissions=frozenset([perm]))
        store.add_role(custom)
        result = store.list_all_roles()
        assert len(result) == 1
        assert result[0].name == "custom-role"

    def test_delete_role_builtin_raises(self):
        """Lines 308-313: deleting builtin role raises CannotModifyBuiltinRoleError."""
        store = InMemoryRoleStore()
        with pytest.raises(CannotModifyBuiltinRoleError):
            store.delete_role("admin")

    def test_delete_role_not_found_raises(self):
        """Lines 314-315: deleting nonexistent role raises RoleNotFoundError."""
        store = InMemoryRoleStore()
        with pytest.raises(RoleNotFoundError):
            store.delete_role("nonexistent")

    def test_delete_role_removes_role_and_assignments(self):
        """Lines 316-321: delete_role removes role and all its assignments."""
        store = InMemoryRoleStore()
        perm = Permission(resource_type="custom", action="do", resource_id="*")
        custom = Role(name="custom-role", permissions=frozenset([perm]))
        store.add_role(custom)
        store.assign_role("user-1", "custom-role")
        store.delete_role("custom-role")
        assert store.get_role("custom-role") is None
        roles = store.get_roles_for_principal("user-1", scope="global")
        # custom-role should be gone
        assert all(r.name != "custom-role" for r in roles)

    def test_update_role_builtin_raises(self):
        """Lines 330-335: updating builtin role raises CannotModifyBuiltinRoleError."""
        store = InMemoryRoleStore()
        with pytest.raises(CannotModifyBuiltinRoleError):
            store.update_role("admin", permissions=[], description="new desc")

    def test_update_role_not_found_raises(self):
        """Lines 336-337: updating nonexistent role raises RoleNotFoundError."""
        store = InMemoryRoleStore()
        with pytest.raises(RoleNotFoundError):
            store.update_role("nonexistent", permissions=[], description="desc")

    def test_update_role_success(self):
        """Lines 338-345: update_role replaces permissions and description."""
        store = InMemoryRoleStore()
        perm = Permission(resource_type="old", action="read", resource_id="*")
        custom = Role(name="custom-role", permissions=frozenset([perm]))
        store.add_role(custom)

        new_perm = Permission(resource_type="new", action="write", resource_id="*")
        updated = store.update_role("custom-role", permissions=[new_perm], description="updated desc")
        assert updated.name == "custom-role"
        assert updated.description == "updated desc"
        assert new_perm in updated.permissions
        # Verify store is updated
        fetched = store.get_role("custom-role")
        assert fetched == updated

    def test_list_assignments(self):
        """Lines 356-360: list_assignments returns scope->role mapping."""
        store = InMemoryRoleStore()
        store.assign_role("user-1", "admin", scope="global")
        store.assign_role("user-1", "viewer", scope="tenant:x")
        assignments = store.list_assignments("user-1")
        assert "global" in assignments
        assert "admin" in assignments["global"]
        assert "tenant:x" in assignments
        assert "viewer" in assignments["tenant:x"]

    def test_list_assignments_empty_principal(self):
        store = InMemoryRoleStore()
        result = store.list_assignments("nonexistent")
        assert result == {}

    def test_clear_assignments(self):
        """Lines 368-371: clear_assignments removes all roles for principal."""
        store = InMemoryRoleStore()
        store.assign_role("user-1", "admin", scope="global")
        store.assign_role("user-1", "viewer", scope="tenant:x")
        store.clear_assignments("user-1")
        assert store.list_assignments("user-1") == {}

    def test_clear_assignments_nonexistent_principal_no_error(self):
        store = InMemoryRoleStore()
        # Should not raise
        store.clear_assignments("nonexistent")
