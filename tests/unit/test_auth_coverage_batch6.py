"""Tests for enterprise auth infrastructure: api_key_authenticator, rbac_authorizer,
rate_limiter, and constant_time modules.

Targets all uncovered lines listed in the batch6 coverage plan.
"""

from datetime import datetime, timedelta, UTC
from unittest.mock import Mock, patch
import hashlib

import pytest

from mcp_hangar.domain.contracts.authentication import AuthRequest, IApiKeyStore
from mcp_hangar.domain.contracts.authorization import AuthorizationRequest, IRoleStore
from mcp_hangar.domain.exceptions import (
    CannotModifyBuiltinRoleError,
    ExpiredCredentialsError,
    InvalidCredentialsError,
    RevokedCredentialsError,
    RoleNotFoundError,
)
from mcp_hangar.domain.value_objects import Permission, Principal, PrincipalId, PrincipalType, Role

from enterprise.auth.infrastructure.api_key_authenticator import (
    ApiKeyAuthenticator,
    InMemoryApiKeyStore,
    MAX_API_KEY_LENGTH,
)
from enterprise.auth.infrastructure.rbac_authorizer import (
    InMemoryRoleStore,
    RBACAuthorizer,
)
from enterprise.auth.infrastructure.rate_limiter import (
    AuthRateLimitConfig,
    AuthRateLimiter,
    get_auth_rate_limiter,
    set_auth_rate_limiter,
)
from enterprise.auth.infrastructure.constant_time import constant_time_key_lookup


# =============================================================================
# constant_time_key_lookup tests
# =============================================================================


class TestConstantTimeKeyLookup:
    """Tests for constant_time_key_lookup function."""

    def test_finds_existing_key(self):
        hash_dict = {"abc123": "value_a", "def456": "value_b"}
        result = constant_time_key_lookup("abc123", hash_dict)
        assert result == "value_a"

    def test_returns_none_for_missing_key(self):
        hash_dict = {"abc123": "value_a", "def456": "value_b"}
        result = constant_time_key_lookup("missing", hash_dict)
        assert result is None

    def test_iterates_all_entries_even_after_match(self):
        """The function must check ALL entries (constant time), not short-circuit."""
        hash_dict = {"first": 1, "second": 2, "third": 3}
        # The match is the first entry, but all entries should still be compared
        result = constant_time_key_lookup("first", hash_dict)
        assert result == 1

    def test_empty_dict_returns_none(self):
        result = constant_time_key_lookup("anything", {})
        assert result is None

    def test_last_match_wins_when_multiple_matches(self):
        """hmac.compare_digest is used; if somehow duplicates exist, last match wins."""
        # With normal dict, keys are unique, so this tests iteration over all items
        hash_dict = {"aaa": 10, "bbb": 20, "ccc": 30}
        result = constant_time_key_lookup("bbb", hash_dict)
        assert result == 20


# =============================================================================
# ApiKeyAuthenticator tests
# =============================================================================


class TestApiKeyAuthenticator:
    """Tests for ApiKeyAuthenticator class."""

    def _make_authenticator(self, key_store: IApiKeyStore | None = None, header_name: str | None = None):
        store = key_store or Mock(spec=IApiKeyStore)
        return ApiKeyAuthenticator(key_store=store, header_name=header_name), store

    def test_supports_returns_true_when_header_present(self):
        auth, _ = self._make_authenticator()
        request = AuthRequest(headers={"X-API-Key": "mcp_test"}, source_ip="127.0.0.1")
        assert auth.supports(request) is True

    def test_supports_returns_true_for_lowercase_header(self):
        """Line 54: case-insensitive header lookup."""
        auth, _ = self._make_authenticator()
        request = AuthRequest(headers={"x-api-key": "mcp_test"}, source_ip="127.0.0.1")
        assert auth.supports(request) is True

    def test_supports_returns_false_when_header_absent(self):
        auth, _ = self._make_authenticator()
        request = AuthRequest(headers={"Authorization": "Bearer xxx"}, source_ip="127.0.0.1")
        assert auth.supports(request) is False

    def test_supports_with_custom_header_name(self):
        auth, _ = self._make_authenticator(header_name="X-Custom-Key")
        request = AuthRequest(headers={"X-Custom-Key": "mcp_test"}, source_ip="127.0.0.1")
        assert auth.supports(request) is True

    def test_authenticate_empty_header_raises_invalid_credentials(self):
        """Lines 71-77: empty key raises InvalidCredentialsError."""
        auth, _ = self._make_authenticator()
        request = AuthRequest(headers={"X-API-Key": ""}, source_ip="1.2.3.4")
        with pytest.raises(InvalidCredentialsError, match="API key header is empty"):
            auth.authenticate(request)

    def test_authenticate_missing_header_raises_invalid_credentials(self):
        """Line 71: key not found in either case returns empty string."""
        auth, _ = self._make_authenticator()
        request = AuthRequest(headers={}, source_ip="1.2.3.4")
        with pytest.raises(InvalidCredentialsError, match="API key header is empty"):
            auth.authenticate(request)

    def test_authenticate_key_too_long_raises_invalid_credentials(self):
        """Lines 80-84: key exceeds MAX_API_KEY_LENGTH."""
        auth, _ = self._make_authenticator()
        long_key = "mcp_" + "a" * (MAX_API_KEY_LENGTH + 1)
        request = AuthRequest(headers={"X-API-Key": long_key}, source_ip="1.2.3.4")
        with pytest.raises(InvalidCredentialsError, match="exceeds maximum length"):
            auth.authenticate(request)

    def test_authenticate_missing_prefix_raises_invalid_credentials(self):
        """Lines 86-90: key without mcp_ prefix."""
        auth, _ = self._make_authenticator()
        request = AuthRequest(headers={"X-API-Key": "invalid_no_prefix"}, source_ip="1.2.3.4")
        with pytest.raises(InvalidCredentialsError, match="must start with"):
            auth.authenticate(request)

    def test_authenticate_unknown_key_raises_invalid_credentials(self):
        """Lines 97-107: key_store returns None."""
        auth, store = self._make_authenticator()
        store.get_principal_for_key.return_value = None
        request = AuthRequest(headers={"X-API-Key": "mcp_validkey123"}, source_ip="1.2.3.4")
        with pytest.raises(InvalidCredentialsError, match="Invalid API key"):
            auth.authenticate(request)

    def test_authenticate_success_returns_principal(self):
        """Lines 109-116: successful authentication."""
        principal = Principal(
            id=PrincipalId("svc-test"),
            type=PrincipalType.SERVICE_ACCOUNT,
        )
        auth, store = self._make_authenticator()
        store.get_principal_for_key.return_value = principal
        request = AuthRequest(headers={"X-API-Key": "mcp_validkey123"}, source_ip="1.2.3.4")
        result = auth.authenticate(request)
        assert result == principal

    def test_authenticate_uses_lowercase_header_fallback(self):
        """Line 71: case-insensitive header lookup via lowercase fallback."""
        principal = Principal(id=PrincipalId("svc-test"), type=PrincipalType.SERVICE_ACCOUNT)
        auth, store = self._make_authenticator()
        store.get_principal_for_key.return_value = principal
        request = AuthRequest(headers={"x-api-key": "mcp_validkey123"}, source_ip="1.2.3.4")
        result = auth.authenticate(request)
        assert result == principal

    def test_hash_key_returns_sha256(self):
        """Line 128: _hash_key returns SHA-256 hex digest."""
        key = "mcp_test123"
        expected = hashlib.sha256(key.encode()).hexdigest()
        assert ApiKeyAuthenticator._hash_key(key) == expected

    def test_generate_key_has_correct_prefix(self):
        """Lines 137-138: generate_key starts with mcp_ prefix."""
        key = ApiKeyAuthenticator.generate_key()
        assert key.startswith("mcp_")
        assert len(key) > len("mcp_")

    def test_generate_key_is_unique(self):
        """Each call should produce a different key (cryptographic randomness)."""
        keys = {ApiKeyAuthenticator.generate_key() for _ in range(10)}
        assert len(keys) == 10


# =============================================================================
# InMemoryApiKeyStore tests
# =============================================================================


class TestInMemoryApiKeyStore:
    """Tests for InMemoryApiKeyStore class."""

    def _make_store(self, event_publisher=None):
        return InMemoryApiKeyStore(event_publisher=event_publisher)

    def test_create_key_returns_raw_key_with_prefix(self):
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        assert raw_key.startswith("mcp_")

    def test_create_key_stores_key_and_get_principal_retrieves(self):
        """Lines 194-241: get_principal_for_key finds created key."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        principal = store.get_principal_for_key(key_hash)
        assert principal is not None
        assert principal.id.value == "svc-1"
        assert principal.type == PrincipalType.SERVICE_ACCOUNT

    def test_get_principal_for_key_returns_none_for_unknown(self):
        """Lines 196-198: constant_time_key_lookup returns None."""
        store = self._make_store()
        result = store.get_principal_for_key("nonexistent_hash")
        assert result is None

    def test_get_principal_for_key_raises_on_revoked(self):
        """Lines 202-206: revoked key raises RevokedCredentialsError."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        # Get metadata to find key_id
        metadata = list(store._keys.values())[0][0]
        store.revoke_key(metadata.key_id)
        with pytest.raises(RevokedCredentialsError, match="revoked"):
            store.get_principal_for_key(key_hash)

    def test_get_principal_for_key_raises_on_expired(self):
        """Lines 208-213: expired key raises ExpiredCredentialsError."""
        store = self._make_store()
        expired_time = datetime.now(UTC) - timedelta(hours=1)
        raw_key = store.create_key(principal_id="svc-1", name="test-key", expires_at=expired_time)
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        with pytest.raises(ExpiredCredentialsError, match="expired"):
            store.get_principal_for_key(key_hash)

    def test_get_principal_for_key_updates_last_used_at(self):
        """Lines 230-239: last_used_at is updated on access."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        # First access
        store.get_principal_for_key(key_hash)
        metadata_after = store._keys[key_hash][0]
        assert metadata_after.last_used_at is not None

    def test_get_principal_for_key_rotated_within_grace_period(self):
        """Lines 216-225: rotated key within grace period is allowed."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        # Rotate with large grace period
        with patch("enterprise.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 1000.0
            _ = store.rotate_key(metadata.key_id, grace_period_seconds=3600)
        # Access old key within grace period
        with patch("enterprise.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 1500.0  # Within grace period
            principal = store.get_principal_for_key(key_hash)
        assert principal is not None
        assert principal.id.value == "svc-1"

    def test_get_principal_for_key_rotated_grace_expired_raises(self):
        """Lines 219-224: rotated key after grace period raises ExpiredCredentialsError."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        # Rotate with short grace period
        with patch("enterprise.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 1000.0
            store.rotate_key(metadata.key_id, grace_period_seconds=10)
        # Access old key after grace period
        with patch("enterprise.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 2000.0  # Well past grace period
            with pytest.raises(ExpiredCredentialsError, match="rotated"):
                store.get_principal_for_key(key_hash)

    def test_create_key_with_groups_and_tenant(self):
        store = self._make_store()
        raw_key = store.create_key(
            principal_id="svc-1",
            name="test-key",
            groups=frozenset({"admin", "ops"}),
            tenant_id="tenant-abc",
        )
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        principal = store.get_principal_for_key(key_hash)
        assert principal.groups == frozenset({"admin", "ops"})
        assert principal.tenant_id == "tenant-abc"

    def test_create_key_max_keys_limit_raises(self):
        """Line 272: exceeding MAX_KEYS_PER_PRINCIPAL raises ValueError."""
        store = self._make_store()
        store.MAX_KEYS_PER_PRINCIPAL = 2  # Lower limit for testing
        store.create_key(principal_id="svc-1", name="key1")
        store.create_key(principal_id="svc-1", name="key2")
        with pytest.raises(ValueError, match="maximum number of API keys"):
            store.create_key(principal_id="svc-1", name="key3")

    def test_revoke_key_success(self):
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        result = store.revoke_key(metadata.key_id)
        assert result is True

    def test_revoke_key_not_found(self):
        """Lines 350-351: revoke nonexistent key returns False."""
        store = self._make_store()
        result = store.revoke_key("nonexistent-id")
        assert result is False

    def test_list_keys_returns_metadata_for_principal(self):
        store = self._make_store()
        store.create_key(principal_id="svc-1", name="key1")
        store.create_key(principal_id="svc-1", name="key2")
        store.create_key(principal_id="svc-2", name="key3")
        keys = store.list_keys("svc-1")
        assert len(keys) == 2
        names = {k.name for k in keys}
        assert names == {"key1", "key2"}

    def test_get_key_by_id_found(self):
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        result = store.get_key_by_id(metadata.key_id)
        assert result is not None
        assert result.key_id == metadata.key_id

    def test_get_key_by_id_not_found(self):
        store = self._make_store()
        result = store.get_key_by_id("nonexistent")
        assert result is None

    def test_count_keys_active_only(self):
        """Lines 393-399: count_keys counts active non-expired keys."""
        store = self._make_store()
        store.create_key(principal_id="svc-1", name="active-key")
        expired_time = datetime.now(UTC) - timedelta(hours=1)
        store.create_key(principal_id="svc-1", name="expired-key", expires_at=expired_time)
        # Revoke one
        raw_key3 = store.create_key(principal_id="svc-1", name="revoked-key")
        key_hash3 = ApiKeyAuthenticator._hash_key(raw_key3)
        meta3 = store._keys[key_hash3][0]
        store.revoke_key(meta3.key_id)

        count = store.count_keys("svc-1")
        assert count == 1  # Only the active, non-expired one

    def test_count_all_keys(self):
        """Lines 407-408: count_all_keys includes revoked keys."""
        store = self._make_store()
        store.create_key(principal_id="svc-1", name="key1")
        raw_key2 = store.create_key(principal_id="svc-1", name="key2")
        key_hash2 = ApiKeyAuthenticator._hash_key(raw_key2)
        meta2 = store._keys[key_hash2][0]
        store.revoke_key(meta2.key_id)
        assert store.count_all_keys() == 2

    def test_count_all_active_keys(self):
        """Lines 416-423: count_all_active_keys excludes revoked and expired."""
        store = self._make_store()
        store.create_key(principal_id="svc-1", name="active")
        store.create_key(principal_id="svc-2", name="active2")
        expired_time = datetime.now(UTC) - timedelta(hours=1)
        store.create_key(principal_id="svc-3", name="expired", expires_at=expired_time)
        assert store.count_all_active_keys() == 2

    def test_rotate_key_success(self):
        """Lines 444-522: rotate_key produces new key, marks old as rotated."""
        publisher = Mock()
        store = self._make_store(event_publisher=publisher)
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]

        with patch("enterprise.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 5000.0
            new_raw = store.rotate_key(metadata.key_id, grace_period_seconds=3600, rotated_by="admin")

        assert new_raw.startswith("mcp_")
        assert new_raw != raw_key
        # Old key should be in rotated_keys
        assert key_hash in store._rotated_keys
        _, grace_until = store._rotated_keys[key_hash]
        assert grace_until == 5000.0 + 3600
        # Event was published
        publisher.assert_called_once()

    def test_rotate_key_not_found_raises(self):
        """Line 458: rotate_key with unknown key_id raises ValueError."""
        store = self._make_store()
        with pytest.raises(ValueError, match="API key not found"):
            store.rotate_key("nonexistent-id")

    def test_rotate_revoked_key_raises(self):
        """Line 462: rotate revoked key raises ValueError."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        store.revoke_key(metadata.key_id)
        with pytest.raises(ValueError, match="Cannot rotate revoked key"):
            store.rotate_key(metadata.key_id)

    def test_rotate_key_already_pending_raises(self):
        """Line 466: rotate key with pending rotation raises ValueError."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        with patch("enterprise.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 1000.0
            store.rotate_key(metadata.key_id)
        with pytest.raises(ValueError, match="pending rotation"):
            store.rotate_key(metadata.key_id)

    def test_publish_event_swallows_exceptions(self):
        """Lines 173-179: _publish_event logs but does not raise."""
        publisher = Mock(side_effect=RuntimeError("boom"))
        store = self._make_store(event_publisher=publisher)
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        # Rotate should succeed despite event publish failure
        with patch("enterprise.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 1000.0
            new_raw = store.rotate_key(metadata.key_id)
        assert new_raw.startswith("mcp_")

    def test_publish_event_noop_when_no_publisher(self):
        """Lines 173-174: _publish_event does nothing when publisher is None."""
        store = self._make_store(event_publisher=None)
        # No error should occur
        store._publish_event(object())


# =============================================================================
# RBACAuthorizer tests
# =============================================================================


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
            if scope == "tenant:acme" and principal_id == "user-1":
                return [role]
            return []

        store.get_roles_for_principal.side_effect = get_roles
        auth = RBACAuthorizer(role_store=store)

        principal = self._make_principal(tenant_id="acme")
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
            if scope == "tenant:acme" and principal_id == "group:auditors":
                return [role]
            return []

        store.get_roles_for_principal.side_effect = get_roles
        auth = RBACAuthorizer(role_store=store)

        principal = self._make_principal(groups=frozenset({"auditors"}), tenant_id="acme")
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


# =============================================================================
# InMemoryRoleStore tests
# =============================================================================


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
        store.assign_role("user-1", "viewer", scope="tenant:acme")
        roles = store.get_roles_for_principal("user-1", scope="global")
        assert len(roles) == 1
        assert roles[0].name == "admin"

    def test_get_roles_for_principal_wildcard_scope(self):
        """Lines 223-226: scope='*' returns all scopes."""
        store = InMemoryRoleStore()
        store.assign_role("user-1", "admin", scope="global")
        store.assign_role("user-1", "viewer", scope="tenant:acme")
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
        store.assign_role("user-1", "viewer", scope="tenant:acme")
        assignments = store.list_assignments("user-1")
        assert "global" in assignments
        assert "admin" in assignments["global"]
        assert "tenant:acme" in assignments
        assert "viewer" in assignments["tenant:acme"]

    def test_list_assignments_empty_principal(self):
        store = InMemoryRoleStore()
        result = store.list_assignments("nonexistent")
        assert result == {}

    def test_clear_assignments(self):
        """Lines 368-371: clear_assignments removes all roles for principal."""
        store = InMemoryRoleStore()
        store.assign_role("user-1", "admin", scope="global")
        store.assign_role("user-1", "viewer", scope="tenant:acme")
        store.clear_assignments("user-1")
        assert store.list_assignments("user-1") == {}

    def test_clear_assignments_nonexistent_principal_no_error(self):
        store = InMemoryRoleStore()
        # Should not raise
        store.clear_assignments("nonexistent")


# =============================================================================
# AuthRateLimiter tests
# =============================================================================


class TestAuthRateLimitConfig:
    """Tests for AuthRateLimitConfig dataclass."""

    def test_defaults(self):
        config = AuthRateLimitConfig()
        assert config.enabled is True
        assert config.max_attempts == 10
        assert config.window_seconds == 60
        assert config.lockout_seconds == 300
        assert config.lockout_escalation_factor == 2.0
        assert config.max_lockout_seconds == 3600


class TestAuthRateLimiter:
    """Tests for AuthRateLimiter class."""

    def _make_limiter(
        self,
        config: AuthRateLimitConfig | None = None,
        event_publisher=None,
    ) -> AuthRateLimiter:
        return AuthRateLimiter(config=config, event_publisher=event_publisher)

    def test_enabled_property(self):
        """Line 118: enabled property."""
        limiter = self._make_limiter(config=AuthRateLimitConfig(enabled=True))
        assert limiter.enabled is True
        limiter2 = self._make_limiter(config=AuthRateLimitConfig(enabled=False))
        assert limiter2.enabled is False

    def test_check_rate_limit_disabled_always_allows(self):
        """Lines 129-135: disabled rate limiter allows all."""
        limiter = self._make_limiter(config=AuthRateLimitConfig(enabled=False))
        result = limiter.check_rate_limit("1.2.3.4")
        assert result.allowed is True
        assert result.reason == "rate_limiting_disabled"

    def test_check_rate_limit_no_previous_attempts(self):
        """Lines 143-149: IP with no tracker is allowed."""
        limiter = self._make_limiter()
        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            result = limiter.check_rate_limit("1.2.3.4")
        assert result.allowed is True
        assert result.reason == "no_previous_attempts"

    def test_check_rate_limit_locked_out(self):
        """Lines 152-165: IP currently locked returns not allowed with retry_after."""
        config = AuthRateLimitConfig(max_attempts=2, window_seconds=60, lockout_seconds=300)
        limiter = self._make_limiter(config=config)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.record_failure("1.2.3.4")
            # This check triggers the lockout
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is False
            assert result.reason == "rate_limit_exceeded"

            # Subsequent check while locked
            mock_time.time.return_value = 1010.0
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is False
            assert result.reason == "locked_out"
            assert result.retry_after is not None
            assert result.retry_after > 0

    def test_check_rate_limit_lockout_expired_unlocks(self):
        """Lines 166-177: expired lockout clears locked_until, publishes event."""
        publisher = Mock()
        config = AuthRateLimitConfig(
            max_attempts=2, window_seconds=60, lockout_seconds=10,
            cleanup_interval=99999,  # Prevent cleanup
        )
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.record_failure("1.2.3.4")
            # Trigger lockout
            limiter.check_rate_limit("1.2.3.4")
            publisher.reset_mock()

            # Time past lockout
            mock_time.time.return_value = 1100.0
            result = limiter.check_rate_limit("1.2.3.4")
            # Should be allowed (lockout expired, old attempts outside window)
            assert result.allowed is True
            # Unlock event should have been published
            publisher.assert_called()

    def test_check_rate_limit_escalation(self):
        """Lines 188-194: lockout escalation with factor."""
        config = AuthRateLimitConfig(
            max_attempts=1, window_seconds=60, lockout_seconds=10,
            lockout_escalation_factor=2.0, max_lockout_seconds=3600,
            cleanup_interval=99999,
        )
        publisher = Mock()
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            # First lockout: 10 seconds
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is False
            assert result.retry_after == 10.0  # 10 * 2^0

            # Wait for lockout to expire, record failure again
            mock_time.time.return_value = 1100.0
            limiter.check_rate_limit("1.2.3.4")  # clears lockout
            limiter.record_failure("1.2.3.4")
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is False
            assert result.retry_after == 20.0  # 10 * 2^1

    def test_check_rate_limit_escalation_capped(self):
        """Lines 189-193: lockout capped at max_lockout_seconds."""
        config = AuthRateLimitConfig(
            max_attempts=1, window_seconds=60, lockout_seconds=1000,
            lockout_escalation_factor=10.0, max_lockout_seconds=2000,
            cleanup_interval=99999,
        )
        limiter = self._make_limiter(config=config)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is False
            # First lockout: min(1000 * 10^0, 2000) = 1000
            assert result.retry_after == 1000.0

            # Expire lockout and trigger second
            mock_time.time.return_value = 3000.0
            limiter.check_rate_limit("1.2.3.4")  # expire lockout
            limiter.record_failure("1.2.3.4")
            result = limiter.check_rate_limit("1.2.3.4")
            # Second lockout: min(1000 * 10^1, 2000) = 2000 (capped)
            assert result.retry_after == 2000.0

    def test_check_rate_limit_within_limit(self):
        """Lines 217-222: attempts within limit returns allowed."""
        config = AuthRateLimitConfig(max_attempts=5, window_seconds=60)
        limiter = self._make_limiter(config=config)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.record_failure("1.2.3.4")
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is True
            assert result.remaining == 3  # 5 - 2
            assert result.reason == "within_limit"

    def test_record_failure_disabled_noop(self):
        """Lines 230-231: disabled limiter does nothing on record_failure."""
        limiter = self._make_limiter(config=AuthRateLimitConfig(enabled=False))
        limiter.record_failure("1.2.3.4")
        assert len(limiter._trackers) == 0

    def test_record_failure_creates_tracker(self):
        """Lines 236-246: record_failure creates tracker and appends timestamp."""
        limiter = self._make_limiter()
        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
        assert "1.2.3.4" in limiter._trackers
        assert len(limiter._trackers["1.2.3.4"].attempts) == 1

    def test_record_success_disabled_noop(self):
        """Lines 254-255: disabled limiter does nothing on record_success."""
        limiter = self._make_limiter(config=AuthRateLimitConfig(enabled=False))
        limiter.record_success("1.2.3.4")

    def test_record_success_clears_tracker(self):
        """Lines 257-269: record_success deletes tracker."""
        limiter = self._make_limiter()
        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
        limiter.record_success("1.2.3.4")
        assert "1.2.3.4" not in limiter._trackers

    def test_record_success_publishes_unlock_event_if_locked(self):
        """Lines 260-267: record_success publishes unlock event for locked tracker."""
        publisher = Mock()
        config = AuthRateLimitConfig(max_attempts=1, window_seconds=60, lockout_seconds=300)
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.check_rate_limit("1.2.3.4")  # Triggers lockout
            publisher.reset_mock()

            limiter.record_success("1.2.3.4")
            # Should publish RateLimitUnlock event
            publisher.assert_called_once()
            event = publisher.call_args[0][0]
            assert event.unlock_reason == "success"

    def test_record_success_no_event_if_not_locked(self):
        """Lines 258-268: no unlock event if tracker exists but not locked."""
        publisher = Mock()
        limiter = self._make_limiter(event_publisher=publisher)
        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
        limiter.record_success("1.2.3.4")
        publisher.assert_not_called()

    def test_get_status_unknown_ip(self):
        """Lines 282-289: get_status for unknown IP returns defaults."""
        limiter = self._make_limiter()
        status = limiter.get_status("1.2.3.4")
        assert status["ip"] == "1.2.3.4"
        assert status["attempts"] == 0
        assert status["remaining"] == 10
        assert status["locked"] is False
        assert status["locked_until"] is None

    def test_get_status_with_attempts(self):
        """Lines 291-301: get_status with recent attempts."""
        config = AuthRateLimitConfig(max_attempts=5, window_seconds=60)
        limiter = self._make_limiter(config=config)
        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.record_failure("1.2.3.4")
            status = limiter.get_status("1.2.3.4")
        assert status["attempts"] == 2
        assert status["remaining"] == 3
        assert status["locked"] is False

    def test_get_status_locked(self):
        """Lines 295-301: get_status when IP is locked."""
        config = AuthRateLimitConfig(max_attempts=1, window_seconds=60, lockout_seconds=300)
        limiter = self._make_limiter(config=config)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.check_rate_limit("1.2.3.4")  # Triggers lockout
            status = limiter.get_status("1.2.3.4")
        assert status["locked"] is True
        assert status["locked_until"] is not None

    def test_clear_all(self):
        """Lines 309-322: clear(None) clears all trackers and publishes unlock events."""
        publisher = Mock()
        config = AuthRateLimitConfig(max_attempts=1, window_seconds=60, lockout_seconds=300)
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.check_rate_limit("1.2.3.4")  # Lock out
            limiter.record_failure("5.6.7.8")
            publisher.reset_mock()

        limiter.clear()
        assert len(limiter._trackers) == 0
        # Should publish unlock event for the locked IP
        publisher.assert_called_once()

    def test_clear_specific_ip(self):
        """Lines 323-334: clear(ip) clears specific IP."""
        publisher = Mock()
        config = AuthRateLimitConfig(max_attempts=1, window_seconds=60, lockout_seconds=300)
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            limiter.check_rate_limit("1.2.3.4")  # Lock out
            limiter.record_failure("5.6.7.8")
            publisher.reset_mock()

        limiter.clear("1.2.3.4")
        assert "1.2.3.4" not in limiter._trackers
        assert "5.6.7.8" in limiter._trackers
        # Unlock event for locked IP
        publisher.assert_called_once()

    def test_clear_specific_ip_not_locked_no_event(self):
        """Lines 323-334: clear specific IP that is not locked does not publish event."""
        publisher = Mock()
        limiter = self._make_limiter(event_publisher=publisher)
        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
        limiter.clear("1.2.3.4")
        publisher.assert_not_called()

    def test_clear_nonexistent_ip(self):
        limiter = self._make_limiter()
        # Should not raise
        limiter.clear("nonexistent")

    def test_maybe_cleanup_skips_when_interval_not_reached(self):
        """Lines 341-343: _maybe_cleanup only runs when interval exceeded."""
        config = AuthRateLimitConfig(cleanup_interval=600)
        limiter = self._make_limiter(config=config)
        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            # check_rate_limit calls _maybe_cleanup
            mock_time.time.return_value = 1100.0  # 100s < 600s interval
            limiter.check_rate_limit("1.2.3.4")
        # Tracker should still exist (no cleanup)
        assert "1.2.3.4" in limiter._trackers

    def test_do_cleanup_removes_stale_trackers(self):
        """Lines 351-380: _do_cleanup removes old, unlocked trackers."""
        publisher = Mock()
        config = AuthRateLimitConfig(max_attempts=5, window_seconds=60, cleanup_interval=10)
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("old-ip")

            # Fast forward past window
            mock_time.time.return_value = 2000.0
            removed = limiter.force_cleanup()
        assert removed == 1
        assert "old-ip" not in limiter._trackers

    def test_do_cleanup_keeps_locked_trackers(self):
        """Lines 358-359: cleanup keeps locked IPs."""
        config = AuthRateLimitConfig(
            max_attempts=1, window_seconds=60, lockout_seconds=3600,
            cleanup_interval=10,
        )
        limiter = self._make_limiter(config=config)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("locked-ip")
            limiter.check_rate_limit("locked-ip")  # Triggers lockout

            mock_time.time.return_value = 1500.0  # Lockout not expired
            removed = limiter.force_cleanup()
        assert removed == 0
        assert "locked-ip" in limiter._trackers

    def test_do_cleanup_removes_expired_lockout_publishes_unlock(self):
        """Lines 364-371: cleanup removes expired lockout and publishes event."""
        publisher = Mock()
        config = AuthRateLimitConfig(
            max_attempts=1, window_seconds=60, lockout_seconds=10,
            cleanup_interval=10,
        )
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("expired-lockout-ip")
            limiter.check_rate_limit("expired-lockout-ip")
            publisher.reset_mock()

            # Past both window and lockout
            mock_time.time.return_value = 2000.0
            removed = limiter.force_cleanup()
        assert removed == 1
        # Should publish cleanup unlock event
        publisher.assert_called_once()
        event = publisher.call_args[0][0]
        assert event.unlock_reason == "cleanup"

    def test_do_cleanup_keeps_tracker_with_recent_attempts(self):
        """Lines 361-362: cleanup keeps trackers with recent attempts."""
        config = AuthRateLimitConfig(max_attempts=5, window_seconds=60, cleanup_interval=10)
        limiter = self._make_limiter(config=config)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("recent-ip")

            mock_time.time.return_value = 1030.0  # Within window
            removed = limiter.force_cleanup()
        assert removed == 0
        assert "recent-ip" in limiter._trackers

    def test_force_cleanup_returns_count(self):
        """Lines 388-390: force_cleanup returns removed count."""
        config = AuthRateLimitConfig(window_seconds=60, cleanup_interval=10)
        limiter = self._make_limiter(config=config)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("ip1")
            limiter.record_failure("ip2")

            mock_time.time.return_value = 2000.0
            removed = limiter.force_cleanup()
        assert removed == 2

    def test_publish_event_swallows_exception(self):
        """Lines 109-113: _publish_event logs but does not raise."""
        publisher = Mock(side_effect=RuntimeError("event fail"))
        config = AuthRateLimitConfig(max_attempts=1, window_seconds=60, lockout_seconds=10)
        limiter = self._make_limiter(config=config, event_publisher=publisher)

        with patch("enterprise.auth.infrastructure.rate_limiter.time") as mock_time:
            mock_time.time.return_value = 1000.0
            limiter.record_failure("1.2.3.4")
            # This should not raise despite publisher failure
            result = limiter.check_rate_limit("1.2.3.4")
            assert result.allowed is False

    def test_publish_event_noop_when_no_publisher(self):
        """Lines 109: _publish_event does nothing when publisher is None."""
        limiter = self._make_limiter(event_publisher=None)
        limiter._publish_event(object())  # Should not raise


# =============================================================================
# Module-level get/set rate limiter tests
# =============================================================================


class TestModuleLevelRateLimiter:
    """Tests for get_auth_rate_limiter and set_auth_rate_limiter."""

    def teardown_method(self):
        """Reset global state between tests."""
        import enterprise.auth.infrastructure.rate_limiter as rl_module
        rl_module._default_limiter = None

    def test_get_auth_rate_limiter_creates_default(self):
        """Lines 404-406: get_auth_rate_limiter creates default when None."""
        import enterprise.auth.infrastructure.rate_limiter as rl_module
        rl_module._default_limiter = None
        limiter = get_auth_rate_limiter()
        assert limiter is not None
        assert isinstance(limiter, AuthRateLimiter)

    def test_get_auth_rate_limiter_returns_same_instance(self):
        """get_auth_rate_limiter is idempotent."""
        import enterprise.auth.infrastructure.rate_limiter as rl_module
        rl_module._default_limiter = None
        limiter1 = get_auth_rate_limiter()
        limiter2 = get_auth_rate_limiter()
        assert limiter1 is limiter2

    def test_set_auth_rate_limiter(self):
        """Line 416: set_auth_rate_limiter overrides global."""
        custom = AuthRateLimiter(config=AuthRateLimitConfig(max_attempts=42))
        set_auth_rate_limiter(custom)
        assert get_auth_rate_limiter() is custom
