"""Tests for SQLite-based auth storage."""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_hangar.domain.value_objects import Permission, Role
from mcp_hangar.infrastructure.auth.sqlite_store import SQLiteApiKeyStore, SQLiteRoleStore


class TestSQLiteApiKeyStore:
    """Tests for SQLiteApiKeyStore."""

    @pytest.fixture
    def store(self):
        """Create a temporary SQLite store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_auth.db"
            store = SQLiteApiKeyStore(db_path)
            store.initialize()
            yield store
            store.close()

    def test_create_and_get_key(self, store):
        """Create a key and retrieve the principal."""
        raw_key = store.create_key(
            principal_id="test-user",
            name="Test Key",
        )

        assert raw_key.startswith("mcp_")

        # Hash the key and look up
        from mcp_hangar.infrastructure.auth.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)

        principal = store.get_principal_for_key(key_hash)
        assert principal is not None
        assert principal.id.value == "test-user"

    def test_key_with_groups_and_tenant(self, store):
        """Key with groups and tenant_id."""
        raw_key = store.create_key(
            principal_id="service:my-service",
            name="Service Key",
            groups=frozenset(["admins", "developers"]),
            tenant_id="tenant-123",
        )

        from mcp_hangar.infrastructure.auth.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)

        principal = store.get_principal_for_key(key_hash)
        assert principal is not None
        assert principal.id.value == "service:my-service"
        assert principal.tenant_id == "tenant-123"
        assert "admins" in principal.groups
        assert "developers" in principal.groups

    def test_expired_key_raises(self, store):
        """Expired key raises ExpiredCredentialsError."""
        from mcp_hangar.domain.exceptions import ExpiredCredentialsError

        raw_key = store.create_key(
            principal_id="test-user",
            name="Expired Key",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )

        from mcp_hangar.infrastructure.auth.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)

        with pytest.raises(ExpiredCredentialsError):
            store.get_principal_for_key(key_hash)

    def test_revoke_key(self, store):
        """Revoked key raises RevokedCredentialsError."""
        from mcp_hangar.domain.exceptions import RevokedCredentialsError

        raw_key = store.create_key(
            principal_id="test-user",
            name="To Revoke",
        )

        # List keys to get key_id
        keys = store.list_keys("test-user")
        assert len(keys) == 1
        key_id = keys[0].key_id

        # Revoke
        result = store.revoke_key(key_id)
        assert result is True

        # Try to authenticate
        from mcp_hangar.infrastructure.auth.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)

        with pytest.raises(RevokedCredentialsError):
            store.get_principal_for_key(key_hash)

    def test_list_keys(self, store):
        """List keys for a principal."""
        store.create_key("user-1", "Key 1")
        store.create_key("user-1", "Key 2")
        store.create_key("user-2", "Key 3")

        keys_1 = store.list_keys("user-1")
        keys_2 = store.list_keys("user-2")

        assert len(keys_1) == 2
        assert len(keys_2) == 1

    def test_count_keys(self, store):
        """Count active keys."""
        store.create_key("user-1", "Key 1")
        store.create_key("user-1", "Key 2")

        assert store.count_keys("user-1") == 2

        # Revoke one
        keys = store.list_keys("user-1")
        store.revoke_key(keys[0].key_id)

        assert store.count_keys("user-1") == 1

    def test_key_last_used_updated(self, store):
        """last_used_at is updated on each lookup."""
        raw_key = store.create_key("test-user", "Test Key")

        from mcp_hangar.infrastructure.auth.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)

        # First lookup
        store.get_principal_for_key(key_hash)

        keys = store.list_keys("test-user")
        assert keys[0].last_used_at is not None

    def test_persistence_across_connections(self):
        """Data persists across store instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "persist_test.db"

            # Create key in first connection
            store1 = SQLiteApiKeyStore(db_path)
            store1.initialize()
            raw_key = store1.create_key("persist-user", "Persist Key")
            store1.close()

            # Read in second connection
            store2 = SQLiteApiKeyStore(db_path)
            store2.initialize()

            from mcp_hangar.infrastructure.auth.api_key_authenticator import ApiKeyAuthenticator

            key_hash = ApiKeyAuthenticator._hash_key(raw_key)

            principal = store2.get_principal_for_key(key_hash)
            assert principal is not None
            assert principal.id.value == "persist-user"
            store2.close()


class TestSQLiteRoleStore:
    """Tests for SQLiteRoleStore."""

    @pytest.fixture
    def store(self):
        """Create a temporary SQLite store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_roles.db"
            store = SQLiteRoleStore(db_path)
            store.initialize()
            yield store
            store.close()

    def test_builtin_roles_available(self, store):
        """Built-in roles are seeded on init."""
        admin = store.get_role("admin")
        assert admin is not None
        assert admin.name == "admin"

        developer = store.get_role("developer")
        assert developer is not None

    def test_assign_and_get_role(self, store):
        """Assign role and retrieve it."""
        store.assign_role("user-1", "developer")

        roles = store.get_roles_for_principal("user-1")
        assert len(roles) == 1
        assert roles[0].name == "developer"

    def test_assign_multiple_roles(self, store):
        """Assign multiple roles to same principal."""
        store.assign_role("user-1", "developer")
        store.assign_role("user-1", "viewer")

        roles = store.get_roles_for_principal("user-1")
        role_names = {r.name for r in roles}
        assert role_names == {"developer", "viewer"}

    def test_scoped_roles(self, store):
        """Roles with different scopes."""
        store.assign_role("user-1", "admin", scope="global")
        store.assign_role("user-1", "developer", scope="tenant:team-a")

        # Global scope gets both global and requested scope
        global_roles = store.get_roles_for_principal("user-1", scope="tenant:team-a")
        role_names = {r.name for r in global_roles}
        assert role_names == {"admin", "developer"}

        # Different scope only gets global
        other_roles = store.get_roles_for_principal("user-1", scope="tenant:team-b")
        role_names = {r.name for r in other_roles}
        assert role_names == {"admin"}

    def test_revoke_role(self, store):
        """Revoke a role."""
        store.assign_role("user-1", "developer")
        store.revoke_role("user-1", "developer")

        roles = store.get_roles_for_principal("user-1")
        assert len(roles) == 0

    def test_add_custom_role(self, store):
        """Add a custom role."""
        custom_role = Role(
            name="custom-role",
            description="A custom role",
            permissions=frozenset(
                [
                    Permission("tool", "invoke", "math:*"),
                ]
            ),
        )

        store.add_role(custom_role)

        retrieved = store.get_role("custom-role")
        assert retrieved is not None
        assert retrieved.name == "custom-role"
        assert len(retrieved.permissions) == 1

    def test_assign_unknown_role_raises(self, store):
        """Assigning unknown role raises ValueError."""
        with pytest.raises(ValueError, match="Unknown role"):
            store.assign_role("user-1", "nonexistent-role")


class TestStorageBootstrap:
    """Test storage selection in bootstrap."""

    def test_memory_storage_default(self):
        """Memory storage is used by default."""
        from mcp_hangar.server.auth_bootstrap import bootstrap_auth
        from mcp_hangar.server.auth_config import AuthConfig

        config = AuthConfig(enabled=True)
        components = bootstrap_auth(config)

        # Should be in-memory store
        from mcp_hangar.infrastructure.auth.api_key_authenticator import InMemoryApiKeyStore

        assert isinstance(components.api_key_store, InMemoryApiKeyStore)

    def test_sqlite_storage(self):
        """SQLite storage can be configured."""
        from mcp_hangar.server.auth_bootstrap import bootstrap_auth
        from mcp_hangar.server.auth_config import AuthConfig, StorageConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"

            config = AuthConfig(
                enabled=True,
                storage=StorageConfig(driver="sqlite", path=str(db_path)),
            )
            components = bootstrap_auth(config)

            # Should be SQLite store
            from mcp_hangar.infrastructure.auth.sqlite_store import SQLiteApiKeyStore

            assert isinstance(components.api_key_store, SQLiteApiKeyStore)
