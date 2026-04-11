"""Tests for auth infrastructure: sqlite_store, projections, event_sourced_store.

Targets uncovered lines listed in the coverage report for batch 5.
"""

from datetime import datetime, timedelta, UTC
from unittest.mock import Mock, MagicMock, patch
import time

import pytest

from mcp_hangar.domain.events import (
    ApiKeyCreated,
    ApiKeyRevoked,
    DomainEvent,
    KeyRotated,
    RoleAssigned,
    RoleRevoked,
)
from mcp_hangar.domain.exceptions import (
    CannotModifyBuiltinRoleError,
    ExpiredCredentialsError,
    RevokedCredentialsError,
    RoleNotFoundError,
)
from mcp_hangar.domain.contracts.authentication import ApiKeyMetadata
from mcp_hangar.domain.contracts.event_store import IEventStore
from mcp_hangar.domain.value_objects import Permission, Principal, PrincipalId, PrincipalType, Role
from mcp_hangar.domain.model.event_sourced_api_key import ApiKeySnapshot, EventSourcedApiKey
from mcp_hangar.domain.model.event_sourced_role_assignment import (
    EventSourcedRoleAssignment,
    RoleAssignmentSnapshot,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    """Return a path to a temporary SQLite database."""
    return tmp_path / "test.db"


@pytest.fixture
def api_key_store(db_path):
    """Create and initialize an SQLiteApiKeyStore."""
    from enterprise.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    store = SQLiteApiKeyStore(db_path=db_path)
    store.initialize()
    return store


@pytest.fixture
def api_key_store_with_publisher(db_path):
    """Create SQLiteApiKeyStore with an event publisher."""
    from enterprise.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    publisher = Mock()
    store = SQLiteApiKeyStore(db_path=db_path, event_publisher=publisher)
    store.initialize()
    return store, publisher


@pytest.fixture
def role_store(db_path):
    """Create and initialize an SQLiteRoleStore."""
    from enterprise.auth.infrastructure.sqlite_store import SQLiteRoleStore

    store = SQLiteRoleStore(db_path=db_path)
    store.initialize()
    return store


@pytest.fixture
def role_store_with_publisher(db_path):
    """Create SQLiteRoleStore with an event publisher."""
    from enterprise.auth.infrastructure.sqlite_store import SQLiteRoleStore

    publisher = Mock()
    store = SQLiteRoleStore(db_path=db_path, event_publisher=publisher)
    store.initialize()
    return store, publisher


@pytest.fixture
def mock_event_store():
    """Create a mock IEventStore for event-sourced store tests."""
    store = Mock(spec=IEventStore)
    store.list_streams.return_value = []
    store.read_stream.return_value = []
    store.get_stream_version.return_value = -1
    store.append.return_value = 1
    return store


# ===========================================================================
# SQLiteApiKeyStore tests
# ===========================================================================


class TestSQLiteApiKeyStoreInitialize:
    """Tests for SQLiteApiKeyStore.initialize()."""

    def test_initialize_creates_tables(self, db_path):
        from enterprise.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        store = SQLiteApiKeyStore(db_path=db_path)
        store.initialize()
        # Verify initialized flag is set
        assert store._initialized is True

    def test_initialize_early_return_when_already_initialized(self, api_key_store):
        """Line 133: early return when _initialized is True."""
        assert api_key_store._initialized is True
        # Calling initialize again should return early without error
        api_key_store.initialize()
        assert api_key_store._initialized is True

    def test_initialize_migration_adds_rotation_columns(self, db_path):
        """Lines 144-151: migration for rotation columns on existing DBs.

        Create DB without rotation columns, then initialize store which
        should add them via ALTER TABLE.
        """
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key_hash TEXT PRIMARY KEY,
                key_id TEXT NOT NULL UNIQUE,
                principal_id TEXT NOT NULL,
                name TEXT NOT NULL,
                tenant_id TEXT,
                groups TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                last_used_at TEXT,
                revoked INTEGER NOT NULL DEFAULT 0,
                revoked_at TEXT,
                metadata TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS roles (
                name TEXT PRIMARY KEY,
                description TEXT,
                permissions TEXT NOT NULL DEFAULT '[]',
                is_builtin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS role_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                principal_id TEXT NOT NULL,
                role_name TEXT NOT NULL REFERENCES roles(name) ON DELETE CASCADE,
                scope TEXT NOT NULL DEFAULT 'global',
                assigned_at TEXT NOT NULL,
                assigned_by TEXT,
                UNIQUE(principal_id, role_name, scope)
            );
            """
        )
        conn.commit()
        conn.close()

        from enterprise.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        store = SQLiteApiKeyStore(db_path=db_path)
        store.initialize()

        # Verify columns now exist by inserting with them
        conn2 = sqlite3.connect(str(db_path))
        conn2.row_factory = sqlite3.Row
        cursor = conn2.execute("PRAGMA table_info(api_keys)")
        columns = {row["name"] for row in cursor.fetchall()}
        conn2.close()

        assert "rotated_to_key_id" in columns
        assert "grace_until" in columns


class TestSQLiteApiKeyStoreGetPrincipal:
    """Tests for SQLiteApiKeyStore.get_principal_for_key()."""

    def test_returns_none_for_unknown_hash(self, api_key_store):
        """Lines 172-175: key not found path with dummy comparison."""
        result = api_key_store.get_principal_for_key("nonexistent_hash")
        assert result is None

    def test_returns_principal_for_valid_key(self, api_key_store):
        """Lines 159-236: successful lookup path."""
        raw_key = api_key_store.create_key(
            principal_id="svc-test",
            name="test-key",
        )
        from enterprise.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        principal = api_key_store.get_principal_for_key(key_hash)

        assert principal is not None
        assert principal.id == PrincipalId("svc-test")
        assert principal.type == PrincipalType.SERVICE_ACCOUNT

    def test_raises_revoked_credentials_for_revoked_key(self, api_key_store):
        """Lines 178-182: revoked key raises RevokedCredentialsError."""
        raw_key = api_key_store.create_key(principal_id="svc-revoke", name="rkey")
        from enterprise.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)

        # Get key_id from list_keys
        keys = api_key_store.list_keys("svc-revoke")
        key_id = keys[0].key_id
        api_key_store.revoke_key(key_id)

        with pytest.raises(RevokedCredentialsError):
            api_key_store.get_principal_for_key(key_hash)

    def test_raises_expired_for_expired_key(self, api_key_store):
        """Lines 203-210: expired key raises ExpiredCredentialsError."""
        past = datetime.now(UTC) - timedelta(hours=1)
        raw_key = api_key_store.create_key(
            principal_id="svc-exp",
            name="exp-key",
            expires_at=past,
        )
        from enterprise.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)

        with pytest.raises(ExpiredCredentialsError):
            api_key_store.get_principal_for_key(key_hash)

    def test_raises_expired_for_rotated_key_past_grace(self, api_key_store):
        """Lines 185-200: rotated key with expired grace period."""
        raw_key = api_key_store.create_key(principal_id="svc-rot", name="rot-key")
        keys = api_key_store.list_keys("svc-rot")
        key_id = keys[0].key_id

        # Rotate with 0 grace period so it expires immediately
        api_key_store.rotate_key(key_id, grace_period_seconds=0)

        from enterprise.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)

        with pytest.raises(ExpiredCredentialsError, match="rotated"):
            api_key_store.get_principal_for_key(key_hash)

    def test_rotated_key_no_grace_period_rejects_immediately(self, db_path):
        """Lines 196-200: rotated key with no grace_until set rejects immediately."""
        import sqlite3

        from enterprise.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        store = SQLiteApiKeyStore(db_path=db_path)
        store.initialize()

        raw_key = store.create_key(principal_id="svc-nograce", name="ngkey")
        from enterprise.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)

        # Manually set rotated_to_key_id without grace_until
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE api_keys SET rotated_to_key_id = ? WHERE key_hash = ?",
            ("new-kid", key_hash),
        )
        conn.commit()
        conn.close()

        # Need fresh connection
        store2 = SQLiteApiKeyStore(db_path=db_path)
        store2.initialize()

        with pytest.raises(ExpiredCredentialsError, match="rotated"):
            store2.get_principal_for_key(key_hash)

    def test_get_principal_parses_groups_and_metadata(self, db_path):
        """Lines 227-236: groups and metadata parsing."""
        import json
        import sqlite3

        from enterprise.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        store = SQLiteApiKeyStore(db_path=db_path)
        store.initialize()

        raw_key = store.create_key(
            principal_id="svc-grp",
            name="grp-key",
            groups=frozenset(["admin", "ops"]),
        )
        from enterprise.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)

        # Set metadata in DB
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE api_keys SET metadata = ? WHERE key_hash = ?",
            (json.dumps({"extra": "data"}), key_hash),
        )
        conn.commit()
        conn.close()

        store2 = SQLiteApiKeyStore(db_path=db_path)
        store2.initialize()
        principal = store2.get_principal_for_key(key_hash)

        assert principal is not None
        assert "admin" in principal.groups
        assert "ops" in principal.groups
        assert principal.metadata.get("extra") == "data"


class TestSQLiteApiKeyStoreCreateKey:
    """Tests for SQLiteApiKeyStore.create_key()."""

    def test_create_key_returns_raw_key(self, api_key_store):
        """Lines 251-315: create key basic path."""
        raw_key = api_key_store.create_key(
            principal_id="svc-create",
            name="my-key",
        )
        assert raw_key.startswith("mcp_")

    def test_create_key_with_expiration(self, api_key_store):
        """create_key with expires_at."""
        future = datetime.now(UTC) + timedelta(days=30)
        raw_key = api_key_store.create_key(
            principal_id="svc-create",
            name="exp-key",
            expires_at=future,
        )
        assert raw_key.startswith("mcp_")

        keys = api_key_store.list_keys("svc-create")
        assert len(keys) == 1
        assert keys[0].expires_at is not None

    def test_create_key_with_groups_and_tenant(self, api_key_store):
        """create_key with groups and tenant_id."""
        raw_key = api_key_store.create_key(
            principal_id="svc-group",
            name="grp-key",
            groups=frozenset(["dev", "ops"]),
            tenant_id="tenant-1",
        )
        assert raw_key.startswith("mcp_")

    def test_create_key_raises_when_max_reached(self, api_key_store):
        """Lines 265-266: max keys per principal."""
        # Temporarily lower limit
        original = api_key_store.MAX_KEYS_PER_PRINCIPAL
        api_key_store.MAX_KEYS_PER_PRINCIPAL = 2

        api_key_store.create_key(principal_id="svc-max", name="k1")
        api_key_store.create_key(principal_id="svc-max", name="k2")

        with pytest.raises(ValueError, match="maximum API keys"):
            api_key_store.create_key(principal_id="svc-max", name="k3")

        api_key_store.MAX_KEYS_PER_PRINCIPAL = original

    def test_create_key_emits_event(self, api_key_store_with_publisher):
        """Lines 304-313: event publishing on create."""
        store, publisher = api_key_store_with_publisher
        store.create_key(
            principal_id="svc-evt",
            name="evt-key",
            created_by="admin",
        )

        publisher.assert_called_once()
        event = publisher.call_args[0][0]
        assert isinstance(event, ApiKeyCreated)
        assert event.principal_id == "svc-evt"
        assert event.created_by == "admin"


class TestSQLiteApiKeyStoreRevokeKey:
    """Tests for SQLiteApiKeyStore.revoke_key()."""

    def test_revoke_existing_key(self, api_key_store):
        """Lines 322-358: revoke an active key."""
        api_key_store.create_key(principal_id="svc-rev", name="rkey")
        keys = api_key_store.list_keys("svc-rev")
        key_id = keys[0].key_id

        result = api_key_store.revoke_key(key_id)
        assert result is True

        keys_after = api_key_store.list_keys("svc-rev")
        assert keys_after[0].revoked is True

    def test_revoke_nonexistent_key_returns_false(self, api_key_store):
        """Lines 344, 359: revoke when key not found."""
        result = api_key_store.revoke_key("nonexistent-id")
        assert result is False

    def test_revoke_already_revoked_returns_false(self, api_key_store):
        """Revoking an already revoked key returns False."""
        api_key_store.create_key(principal_id="svc-dbl", name="dkey")
        keys = api_key_store.list_keys("svc-dbl")
        key_id = keys[0].key_id

        api_key_store.revoke_key(key_id)
        result = api_key_store.revoke_key(key_id)
        assert result is False

    def test_revoke_emits_event(self, api_key_store_with_publisher):
        """Lines 348-356: event publishing on revoke."""
        store, publisher = api_key_store_with_publisher
        store.create_key(principal_id="svc-revt", name="rkey")
        publisher.reset_mock()

        keys = store.list_keys("svc-revt")
        key_id = keys[0].key_id
        store.revoke_key(key_id, revoked_by="admin-user", reason="test reason")

        publisher.assert_called_once()
        event = publisher.call_args[0][0]
        assert isinstance(event, ApiKeyRevoked)
        assert event.revoked_by == "admin-user"
        assert event.reason == "test reason"


class TestSQLiteApiKeyStoreListKeys:
    """Tests for SQLiteApiKeyStore.list_keys()."""

    def test_list_keys_empty(self, api_key_store):
        """Lines 363-376: list_keys with no keys."""
        result = api_key_store.list_keys("no-one")
        assert result == []

    def test_list_keys_returns_metadata(self, api_key_store):
        """Lines 363-387: list_keys returns ApiKeyMetadata objects."""
        api_key_store.create_key(principal_id="svc-list", name="key-a")
        api_key_store.create_key(principal_id="svc-list", name="key-b")

        keys = api_key_store.list_keys("svc-list")
        assert len(keys) == 2
        assert all(isinstance(k, ApiKeyMetadata) for k in keys)
        names = {k.name for k in keys}
        assert "key-a" in names
        assert "key-b" in names


class TestSQLiteApiKeyStoreCountKeys:
    """Tests for SQLiteApiKeyStore.count_keys()."""

    def test_count_keys_zero(self, api_key_store):
        """Lines 391-401: count_keys with no keys."""
        assert api_key_store.count_keys("nobody") == 0

    def test_count_keys_excludes_revoked(self, api_key_store):
        """count_keys only counts active (non-revoked) keys."""
        api_key_store.create_key(principal_id="svc-cnt", name="k1")
        api_key_store.create_key(principal_id="svc-cnt", name="k2")
        keys = api_key_store.list_keys("svc-cnt")
        api_key_store.revoke_key(keys[0].key_id)

        assert api_key_store.count_keys("svc-cnt") == 1


class TestSQLiteApiKeyStoreRotateKey:
    """Tests for SQLiteApiKeyStore.rotate_key()."""

    def test_rotate_key_returns_new_raw_key(self, api_key_store):
        """Lines 422-517: successful rotation."""
        api_key_store.create_key(principal_id="svc-rot", name="rkey")
        keys = api_key_store.list_keys("svc-rot")
        key_id = keys[0].key_id

        new_raw = api_key_store.rotate_key(key_id, grace_period_seconds=3600)
        assert new_raw.startswith("mcp_")

        # Should have 2 keys now (old + new)
        keys_after = api_key_store.list_keys("svc-rot")
        assert len(keys_after) == 2

    def test_rotate_nonexistent_key_raises(self, api_key_store):
        """Lines 438-439: rotate raises for unknown key."""
        with pytest.raises(ValueError, match="not found"):
            api_key_store.rotate_key("does-not-exist")

    def test_rotate_revoked_key_raises(self, api_key_store):
        """Lines 441-442: rotate raises for revoked key."""
        api_key_store.create_key(principal_id="svc-rotr", name="rkey")
        keys = api_key_store.list_keys("svc-rotr")
        key_id = keys[0].key_id
        api_key_store.revoke_key(key_id)

        with pytest.raises(ValueError, match="revoked"):
            api_key_store.rotate_key(key_id)

    def test_rotate_already_pending_raises(self, api_key_store):
        """Lines 445-448: rotate raises when pending rotation exists."""
        api_key_store.create_key(principal_id="svc-pend", name="pkey")
        keys = api_key_store.list_keys("svc-pend")
        key_id = keys[0].key_id

        # First rotation with long grace
        api_key_store.rotate_key(key_id, grace_period_seconds=86400)

        # Second rotation on same key should fail (pending)
        with pytest.raises(ValueError, match="pending rotation"):
            api_key_store.rotate_key(key_id)

    def test_rotate_emits_key_rotated_event(self, api_key_store_with_publisher):
        """Lines 500-510: event publishing on rotation."""
        store, publisher = api_key_store_with_publisher
        store.create_key(principal_id="svc-rote", name="rkey")
        publisher.reset_mock()

        keys = store.list_keys("svc-rote")
        key_id = keys[0].key_id
        store.rotate_key(key_id, rotated_by="admin-rot")

        publisher.assert_called_once()
        event = publisher.call_args[0][0]
        assert isinstance(event, KeyRotated)
        assert event.rotated_by == "admin-rot"


class TestSQLiteApiKeyStoreClose:
    """Tests for SQLiteApiKeyStore.close()."""

    def test_close_resets_initialized_flag(self, db_path):
        """Lines 521-528: close method."""
        from enterprise.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        store = SQLiteApiKeyStore(db_path=db_path)
        store.initialize()
        assert store._initialized is True

        store.close()
        assert store._initialized is False

    def test_close_when_no_connection(self, db_path):
        """Close when no connection exists does not raise."""
        from enterprise.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        store = SQLiteApiKeyStore(db_path=db_path)
        # Never opened connection
        store.close()
        assert store._initialized is False


# ===========================================================================
# SQLiteRoleStore tests
# ===========================================================================


class TestSQLiteRoleStoreInitialize:
    """Tests for SQLiteRoleStore.initialize()."""

    def test_initialize_seeds_builtin_roles(self, role_store):
        """Lines 567-595: initialize creates tables and seeds builtin roles."""
        from enterprise.auth.roles import BUILTIN_ROLES

        for role_name in BUILTIN_ROLES:
            role = role_store.get_role(role_name)
            assert role is not None
            assert role.name == role_name

    def test_initialize_early_return_when_already_initialized(self, role_store):
        """Line 570: early return when _initialized is True."""
        assert role_store._initialized is True
        role_store.initialize()
        assert role_store._initialized is True


class TestSQLiteRoleStoreGetRole:
    """Tests for SQLiteRoleStore.get_role()."""

    def test_get_role_returns_none_for_unknown(self, role_store):
        """Lines 610-612: role not found returns None."""
        result = role_store.get_role("nonexistent-role")
        assert result is None

    def test_get_role_returns_builtin_role(self, role_store):
        """Lines 599-624: get_role returns a Role with permissions."""
        role = role_store.get_role("admin")
        assert role is not None
        assert role.name == "admin"
        assert len(role.permissions) > 0


class TestSQLiteRoleStoreAddRole:
    """Tests for SQLiteRoleStore.add_role()."""

    def test_add_custom_role(self, role_store):
        """Lines 626-647: add a custom role."""
        custom = Role(
            name="custom-role",
            description="A custom test role",
            permissions=frozenset([
                Permission(resource_type="tool", action="invoke", resource_id="*"),
            ]),
        )
        role_store.add_role(custom)

        fetched = role_store.get_role("custom-role")
        assert fetched is not None
        assert fetched.name == "custom-role"
        assert fetched.description == "A custom test role"


class TestSQLiteRoleStoreGetRolesForPrincipal:
    """Tests for SQLiteRoleStore.get_roles_for_principal()."""

    def test_no_roles_assigned(self, role_store):
        """Empty list when no roles assigned."""
        result = role_store.get_roles_for_principal("nobody")
        assert result == []

    def test_roles_with_wildcard_scope(self, role_store):
        """Lines 657-666: scope='*' returns all roles."""
        role_store.assign_role("svc-1", "admin")
        role_store.assign_role("svc-1", "viewer", scope="tenant:abc")

        roles = role_store.get_roles_for_principal("svc-1", scope="*")
        role_names = {r.name for r in roles}
        assert "admin" in role_names
        assert "viewer" in role_names

    def test_roles_with_specific_scope(self, role_store):
        """Lines 667-676: specific scope returns matching + global."""
        role_store.assign_role("svc-2", "admin", scope="global")
        role_store.assign_role("svc-2", "viewer", scope="tenant:abc")
        role_store.assign_role("svc-2", "developer", scope="tenant:xyz")

        roles = role_store.get_roles_for_principal("svc-2", scope="tenant:abc")
        role_names = {r.name for r in roles}
        assert "admin" in role_names  # global scope included
        assert "viewer" in role_names  # matches tenant:abc
        assert "developer" not in role_names  # different scope


class TestSQLiteRoleStoreAssignRole:
    """Tests for SQLiteRoleStore.assign_role()."""

    def test_assign_role_to_principal(self, role_store):
        """Lines 693-734: assign role."""
        role_store.assign_role("svc-assign", "viewer")
        roles = role_store.get_roles_for_principal("svc-assign")
        assert len(roles) == 1
        assert roles[0].name == "viewer"

    def test_assign_unknown_role_raises(self, role_store):
        """Lines 708-709: assign unknown role raises ValueError."""
        with pytest.raises(ValueError, match="Unknown role"):
            role_store.assign_role("svc-assign", "nonexistent-role")

    def test_assign_duplicate_is_noop(self, role_store):
        """Lines 722-723: duplicate assignment does not emit event."""
        role_store.assign_role("svc-dup", "viewer")
        role_store.assign_role("svc-dup", "viewer")  # should be ignored
        roles = role_store.get_roles_for_principal("svc-dup")
        assert len(roles) == 1

    def test_assign_emits_event(self, role_store_with_publisher):
        """Lines 726-734: event publishing on assign."""
        store, publisher = role_store_with_publisher
        store.assign_role("svc-aevt", "viewer", assigned_by="admin")

        publisher.assert_called_once()
        event = publisher.call_args[0][0]
        assert isinstance(event, RoleAssigned)
        assert event.principal_id == "svc-aevt"
        assert event.assigned_by == "admin"


class TestSQLiteRoleStoreRevokeRole:
    """Tests for SQLiteRoleStore.revoke_role()."""

    def test_revoke_assigned_role(self, role_store):
        """Lines 736-770: revoke role from principal."""
        role_store.assign_role("svc-revr", "viewer")
        role_store.revoke_role("svc-revr", "viewer")
        roles = role_store.get_roles_for_principal("svc-revr")
        assert len(roles) == 0

    def test_revoke_non_assigned_is_noop(self, role_store):
        """Lines 759: revoke when not assigned (rowcount == 0)."""
        role_store.revoke_role("svc-norev", "viewer")
        # Should not raise

    def test_revoke_emits_event(self, role_store_with_publisher):
        """Lines 762-770: event publishing on revoke."""
        store, publisher = role_store_with_publisher
        store.assign_role("svc-revt", "viewer")
        publisher.reset_mock()

        store.revoke_role("svc-revt", "viewer", revoked_by="admin")
        publisher.assert_called_once()
        event = publisher.call_args[0][0]
        assert isinstance(event, RoleRevoked)
        assert event.revoked_by == "admin"


class TestSQLiteRoleStoreListAllRoles:
    """Tests for SQLiteRoleStore.list_all_roles()."""

    def test_list_all_roles_returns_only_custom(self, role_store):
        """Lines 772-788: list_all_roles excludes builtins."""
        custom = Role(
            name="my-custom",
            description="custom",
            permissions=frozenset([Permission(resource_type="tool", action="read", resource_id="*")]),
        )
        role_store.add_role(custom)

        custom_roles = role_store.list_all_roles()
        names = {r.name for r in custom_roles}
        assert "my-custom" in names
        # Builtins should not appear
        assert "admin" not in names


class TestSQLiteRoleStoreDeleteRole:
    """Tests for SQLiteRoleStore.delete_role()."""

    def test_delete_custom_role(self, role_store):
        """Lines 790-809: delete a custom role."""
        custom = Role(name="to-delete", description="", permissions=frozenset())
        role_store.add_role(custom)
        role_store.delete_role("to-delete")

        assert role_store.get_role("to-delete") is None

    def test_delete_builtin_role_raises(self, role_store):
        """Lines 799-800: cannot delete builtin role."""
        with pytest.raises(CannotModifyBuiltinRoleError):
            role_store.delete_role("admin")

    def test_delete_nonexistent_role_raises(self, role_store):
        """Lines 804-805: role not found raises."""
        with pytest.raises(RoleNotFoundError):
            role_store.delete_role("phantom")


class TestSQLiteRoleStoreUpdateRole:
    """Tests for SQLiteRoleStore.update_role()."""

    def test_update_custom_role(self, role_store):
        """Lines 811-843: update a custom role."""
        custom = Role(name="updatable", description="old", permissions=frozenset())
        role_store.add_role(custom)

        new_perms = [Permission(resource_type="tool", action="write", resource_id="*")]
        updated = role_store.update_role("updatable", permissions=new_perms, description="new desc")

        assert updated.description == "new desc"
        assert len(updated.permissions) == 1

        # Verify persisted
        fetched = role_store.get_role("updatable")
        assert fetched.description == "new desc"

    def test_update_builtin_role_raises(self, role_store):
        """Lines 821-822: cannot update builtin."""
        with pytest.raises(CannotModifyBuiltinRoleError):
            role_store.update_role("admin", permissions=[], description="hacked")

    def test_update_nonexistent_role_raises(self, role_store):
        """Lines 826-827: role not found."""
        with pytest.raises(RoleNotFoundError):
            role_store.update_role("phantom", permissions=[], description="")


class TestSQLiteRoleStoreClose:
    """Tests for SQLiteRoleStore.close()."""

    def test_close_resets_initialized_flag(self, db_path):
        """Lines 845-856: close method."""
        from enterprise.auth.infrastructure.sqlite_store import SQLiteRoleStore

        store = SQLiteRoleStore(db_path=db_path)
        store.initialize()
        store.close()
        assert store._initialized is False

    def test_close_when_no_connection(self, db_path):
        """Close without connection does not raise."""
        from enterprise.auth.infrastructure.sqlite_store import SQLiteRoleStore

        store = SQLiteRoleStore(db_path=db_path)
        store.close()
        assert store._initialized is False


# ===========================================================================
# AuthProjection tests
# ===========================================================================


class TestAuthProjection:
    """Tests for AuthProjection read model."""

    def test_apply_api_key_created(self):
        """Lines 122-144: apply ApiKeyCreated event."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        event = ApiKeyCreated(
            key_id="kid-1",
            principal_id="svc-1",
            key_name="test-key",
            expires_at=None,
            created_by="admin",
        )
        proj.apply(event)

        model = proj.get_key_by_id("kid-1")
        assert model is not None
        assert model.key_id == "kid-1"
        assert model.principal_id == "svc-1"
        assert model.name == "test-key"
        assert model.revoked is False

    def test_apply_api_key_revoked(self):
        """Lines 146-158: apply ApiKeyRevoked event."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            ApiKeyCreated(
                key_id="kid-2",
                principal_id="svc-2",
                key_name="to-revoke",
                expires_at=None,
                created_by="admin",
            )
        )
        proj.apply(
            ApiKeyRevoked(
                key_id="kid-2",
                principal_id="svc-2",
                revoked_by="security",
                reason="compromised",
            )
        )

        model = proj.get_key_by_id("kid-2")
        assert model is not None
        assert model.revoked is True
        assert model.revoked_by == "security"
        assert model.revocation_reason == "compromised"

    def test_apply_api_key_revoked_for_unknown_key_is_noop(self):
        """ApiKeyRevoked for unknown key_id does not crash."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            ApiKeyRevoked(
                key_id="unknown",
                principal_id="svc-x",
                revoked_by="admin",
                reason="",
            )
        )
        assert proj.get_key_by_id("unknown") is None

    def test_apply_role_assigned(self):
        """Lines 160-184: apply RoleAssigned event."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        event = RoleAssigned(
            principal_id="svc-3",
            role_name="admin",
            scope="global",
            assigned_by="system",
        )
        proj.apply(event)

        assignments = proj.get_roles_for_principal("svc-3")
        assert len(assignments) == 1
        assert assignments[0].role_name == "admin"

    def test_apply_role_assigned_idempotent(self):
        """Lines 175-183: duplicate assignment is ignored."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        event = RoleAssigned(
            principal_id="svc-idem",
            role_name="viewer",
            scope="global",
            assigned_by="system",
        )
        proj.apply(event)
        proj.apply(event)

        assignments = proj.get_roles_for_principal("svc-idem")
        assert len(assignments) == 1

    def test_apply_role_revoked(self):
        """Lines 186-194: apply RoleRevoked event."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            RoleAssigned(
                principal_id="svc-4",
                role_name="viewer",
                scope="global",
                assigned_by="system",
            )
        )
        proj.apply(
            RoleRevoked(
                principal_id="svc-4",
                role_name="viewer",
                scope="global",
                revoked_by="admin",
            )
        )

        assignments = proj.get_roles_for_principal("svc-4")
        assert len(assignments) == 0

    def test_apply_role_revoked_for_unknown_principal_is_noop(self):
        """RoleRevoked for unknown principal does not crash."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            RoleRevoked(
                principal_id="nobody",
                role_name="admin",
                scope="global",
                revoked_by="system",
            )
        )

    def test_get_keys_for_principal(self):
        """Lines 205-209: get keys for a principal."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            ApiKeyCreated(
                key_id="kid-a",
                principal_id="svc-5",
                key_name="key-a",
                expires_at=None,
                created_by="admin",
            )
        )
        proj.apply(
            ApiKeyCreated(
                key_id="kid-b",
                principal_id="svc-5",
                key_name="key-b",
                expires_at=None,
                created_by="admin",
            )
        )

        keys = proj.get_keys_for_principal("svc-5")
        assert len(keys) == 2

    def test_get_active_key_count(self):
        """Lines 211-214: count active (non-revoked) keys."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            ApiKeyCreated(
                key_id="kid-c",
                principal_id="svc-6",
                key_name="key-c",
                expires_at=None,
                created_by="admin",
            )
        )
        proj.apply(
            ApiKeyCreated(
                key_id="kid-d",
                principal_id="svc-6",
                key_name="key-d",
                expires_at=None,
                created_by="admin",
            )
        )
        proj.apply(
            ApiKeyRevoked(
                key_id="kid-c",
                principal_id="svc-6",
                revoked_by="admin",
                reason="",
            )
        )

        assert proj.get_active_key_count("svc-6") == 1

    def test_has_role_with_wildcard_scope(self):
        """Lines 221-228: has_role with scope='*'."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            RoleAssigned(
                principal_id="svc-7",
                role_name="developer",
                scope="tenant:abc",
                assigned_by="system",
            )
        )

        assert proj.has_role("svc-7", "developer") is True
        assert proj.has_role("svc-7", "developer", scope="*") is True
        assert proj.has_role("svc-7", "admin") is False

    def test_has_role_with_specific_scope(self):
        """has_role with specific scope matches global too."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            RoleAssigned(
                principal_id="svc-8",
                role_name="viewer",
                scope="global",
                assigned_by="system",
            )
        )

        assert proj.has_role("svc-8", "viewer", scope="tenant:abc") is True

    def test_get_stats(self):
        """Lines 234-250: get projection statistics."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        proj.apply(
            ApiKeyCreated(
                key_id="kid-s1",
                principal_id="svc-s",
                key_name="stat-key",
                expires_at=None,
                created_by="admin",
            )
        )
        proj.apply(
            RoleAssigned(
                principal_id="svc-s",
                role_name="admin",
                scope="global",
                assigned_by="system",
            )
        )

        stats = proj.get_stats()
        assert stats["total_api_keys"] == 1
        assert stats["active_api_keys"] == 1
        assert stats["revoked_api_keys"] == 0
        assert stats["total_principals_with_keys"] == 1
        assert stats["total_role_assignments"] == 1
        assert stats["total_principals_with_roles"] == 1

    def test_catchup_without_event_store_returns_zero(self):
        """Lines 90-91: catchup with no event_store returns 0."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        assert proj.catchup() == 0

    def test_catchup_processes_events_from_store(self):
        """Lines 82-105: catchup reads from event store."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        mock_store = Mock(spec=IEventStore)
        event = ApiKeyCreated(
            key_id="kid-cu",
            principal_id="svc-cu",
            key_name="catchup-key",
            expires_at=None,
            created_by="system",
        )
        mock_store.read_all.return_value = iter([(1, "api_key:abc", event)])

        proj = AuthProjection(event_store=mock_store)
        count = proj.catchup()

        assert count == 1
        assert proj.get_key_by_id("kid-cu") is not None

    def test_apply_unrecognized_event_is_noop(self):
        """Lines 107-120: apply with unrecognized event type does nothing."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()

        class UnknownEvent(DomainEvent):
            def __init__(self):
                super().__init__()

        proj.apply(UnknownEvent())
        # Should not raise, stats should be empty
        stats = proj.get_stats()
        assert stats["total_api_keys"] == 0

    def test_api_key_created_with_expiry(self):
        """ApiKeyCreated event with expires_at set."""
        from enterprise.auth.infrastructure.projections import AuthProjection

        proj = AuthProjection()
        future_ts = (datetime.now(UTC) + timedelta(days=30)).timestamp()
        proj.apply(
            ApiKeyCreated(
                key_id="kid-exp",
                principal_id="svc-exp",
                key_name="exp-key",
                expires_at=future_ts,
                created_by="admin",
            )
        )

        model = proj.get_key_by_id("kid-exp")
        assert model.expires_at is not None


# ===========================================================================
# AuthAuditLog tests
# ===========================================================================


class TestAuthAuditLog:
    """Tests for AuthAuditLog projection."""

    def test_apply_api_key_created_creates_entry(self):
        """Lines 282-293: audit entry for ApiKeyCreated."""
        from enterprise.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        log.apply(
            ApiKeyCreated(
                key_id="kid-al",
                principal_id="svc-al",
                key_name="audit-key",
                expires_at=None,
                created_by="admin",
            )
        )

        entries = log.query()
        assert len(entries) == 1
        assert entries[0]["event_type"] == "api_key_created"

    def test_apply_api_key_revoked_creates_entry(self):
        """Lines 295-305: audit entry for ApiKeyRevoked."""
        from enterprise.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        log.apply(
            ApiKeyRevoked(
                key_id="kid-rev",
                principal_id="svc-rev",
                revoked_by="admin",
                reason="test",
            )
        )

        entries = log.query()
        assert len(entries) == 1
        assert entries[0]["event_type"] == "api_key_revoked"

    def test_apply_role_assigned_creates_entry(self):
        """Lines 307-317: audit entry for RoleAssigned."""
        from enterprise.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        log.apply(
            RoleAssigned(
                principal_id="svc-ra",
                role_name="admin",
                scope="global",
                assigned_by="system",
            )
        )

        entries = log.query()
        assert len(entries) == 1
        assert entries[0]["event_type"] == "role_assigned"

    def test_apply_role_revoked_creates_entry(self):
        """Lines 319-329: audit entry for RoleRevoked."""
        from enterprise.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        log.apply(
            RoleRevoked(
                principal_id="svc-rr",
                role_name="admin",
                scope="global",
                revoked_by="system",
            )
        )

        entries = log.query()
        assert len(entries) == 1
        assert entries[0]["event_type"] == "role_revoked"

    def test_apply_unknown_event_returns_none(self):
        """Lines 331: unknown event returns None from _event_to_entry."""
        from enterprise.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()

        class SomeOtherEvent(DomainEvent):
            def __init__(self):
                super().__init__()

        log.apply(SomeOtherEvent())
        entries = log.query()
        assert len(entries) == 0

    def test_query_filter_by_principal(self):
        """Lines 354-356: query with principal_id filter."""
        from enterprise.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        log.apply(
            ApiKeyCreated(key_id="k1", principal_id="svc-a", key_name="k", expires_at=None, created_by="admin")
        )
        log.apply(
            ApiKeyCreated(key_id="k2", principal_id="svc-b", key_name="k", expires_at=None, created_by="admin")
        )

        entries = log.query(principal_id="svc-a")
        assert len(entries) == 1
        assert entries[0]["details"]["key_id"] == "k1"

    def test_query_filter_by_event_type(self):
        """Lines 357-358: query with event_type filter."""
        from enterprise.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        log.apply(
            ApiKeyCreated(key_id="k1", principal_id="svc-a", key_name="k", expires_at=None, created_by="admin")
        )
        log.apply(
            RoleAssigned(principal_id="svc-a", role_name="admin", scope="global", assigned_by="system")
        )

        entries = log.query(event_type="role_assigned")
        assert len(entries) == 1
        assert entries[0]["event_type"] == "role_assigned"

    def test_query_filter_by_since(self):
        """Lines 359-360: query with since filter."""
        from enterprise.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()

        event1 = ApiKeyCreated(key_id="k1", principal_id="svc-a", key_name="k", expires_at=None, created_by="admin")
        # Override occurred_at for predictable test
        event1.occurred_at = 1000.0
        log.apply(event1)

        event2 = ApiKeyCreated(key_id="k2", principal_id="svc-a", key_name="k", expires_at=None, created_by="admin")
        event2.occurred_at = 2000.0
        log.apply(event2)

        entries = log.query(since=1500.0)
        assert len(entries) == 1
        assert entries[0]["details"]["key_id"] == "k2"

    def test_query_limit(self):
        """Lines 363-364: query with limit."""
        from enterprise.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog()
        for i in range(10):
            log.apply(
                ApiKeyCreated(
                    key_id=f"k-{i}",
                    principal_id="svc-a",
                    key_name="k",
                    expires_at=None,
                    created_by="admin",
                )
            )

        entries = log.query(limit=3)
        assert len(entries) == 3

    def test_max_entries_trim(self):
        """Lines 277-278: entries are trimmed when over max."""
        from enterprise.auth.infrastructure.projections import AuthAuditLog

        log = AuthAuditLog(max_entries=5)
        for i in range(10):
            log.apply(
                ApiKeyCreated(
                    key_id=f"k-{i}",
                    principal_id="svc-a",
                    key_name="k",
                    expires_at=None,
                    created_by="admin",
                )
            )

        entries = log.query(limit=100)
        assert len(entries) == 5


# ===========================================================================
# EventSourcedApiKeyStore tests
# ===========================================================================


class TestEventSourcedApiKeyStoreLoadKey:
    """Tests for EventSourcedApiKeyStore._load_key() edge cases."""

    def test_load_key_without_index_entry_and_without_snapshot(self, mock_event_store):
        """Lines 150-153, 159-165: _load_key without index_entry, rebuilds from events."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        creation_event = ApiKeyCreated(
            key_id="kid-1",
            principal_id="svc-1",
            key_name="test-key",
            expires_at=None,
            created_by="admin",
        )

        mock_event_store.list_streams.return_value = ["api_key:hash123"]
        mock_event_store.read_stream.return_value = [creation_event]

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        key = store._load_key("hash123")

        assert key is not None
        assert key.key_id == "kid-1"

    def test_load_key_returns_none_when_no_events_no_snapshot(self, mock_event_store):
        """Lines 142-143: no events and no snapshot returns None."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        mock_event_store.read_stream.return_value = []

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        key = store._load_key("unknown-hash")

        assert key is None

    def test_load_key_returns_none_when_no_creation_event(self, mock_event_store):
        """Lines 162-163: no creation event in stream returns None."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        # Return a non-creation event
        revoke_event = ApiKeyRevoked(
            key_id="kid-x",
            principal_id="svc-x",
            revoked_by="admin",
            reason="",
        )

        mock_event_store.list_streams.return_value = ["api_key:hash456"]
        mock_event_store.read_stream.return_value = [revoke_event]

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        # Build index -- the revoke event won't match ApiKeyCreated so index will be empty
        key = store._load_key("hash456")

        assert key is None

    def test_load_key_with_snapshot(self, mock_event_store):
        """Lines 155-156: load key from snapshot."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        snapshot = ApiKeySnapshot(
            key_hash="snap-hash",
            key_id="snap-kid",
            principal_id="svc-snap",
            name="snap-key",
            tenant_id=None,
            groups=[],
            created_at=time.time(),
            expires_at=None,
            last_used_at=None,
            revoked=False,
            revoked_at=None,
            rotated_to_key_id=None,
            grace_until=None,
            version=5,
        )

        mock_event_store.read_stream.return_value = []

        store = EventSourcedApiKeyStore(
            event_store=mock_event_store,
            snapshot_store={"snap-hash": snapshot},
        )
        key = store._load_key("snap-hash", index_entry=("snap-kid", "svc-snap"))

        assert key is not None
        assert key.key_id == "snap-kid"


class TestEventSourcedApiKeyStorePublishEvents:
    """Tests for EventSourcedApiKeyStore._publish_events()."""

    def test_publish_events_with_publisher(self, mock_event_store):
        """Lines 181-182: events published when publisher is set."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        publisher = Mock()
        store = EventSourcedApiKeyStore(
            event_store=mock_event_store,
            event_publisher=publisher,
        )

        event = ApiKeyCreated(
            key_id="kid-pub",
            principal_id="svc-pub",
            key_name="pub-key",
            expires_at=None,
            created_by="admin",
        )
        store._publish_events([event])

        publisher.publish.assert_called_once_with(event)

    def test_publish_events_without_publisher(self, mock_event_store):
        """No error when publisher is None."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        # Should not raise
        store._publish_events([ApiKeyCreated(
            key_id="kid",
            principal_id="svc",
            key_name="key",
            expires_at=None,
            created_by="admin",
        )])


class TestEventSourcedApiKeyStoreSaveKey:
    """Tests for EventSourcedApiKeyStore._save_key()."""

    def test_save_key_updates_index(self, mock_event_store):
        """Lines 205, 217-222: save_key updates the index."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        store._index = {}
        store._principal_index = {}

        key = EventSourcedApiKey.create(
            key_hash="save-hash",
            key_id="save-kid",
            principal_id="svc-save",
            name="save-key",
            created_by="admin",
        )

        store._save_key(key)

        assert "save-hash" in store._index
        assert "save-hash" in store._principal_index["svc-save"]

    def test_save_key_no_events_returns_early(self, mock_event_store):
        """Lines 204-205: no events to save returns early."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        key = EventSourcedApiKey(
            key_hash="empty-hash",
            key_id="empty-kid",
            principal_id="svc-empty",
            name="empty-key",
        )
        # No events recorded (no create or command called)
        store._save_key(key)

        mock_event_store.append.assert_not_called()


class TestEventSourcedApiKeyStoreGetPrincipal:
    """Tests for EventSourcedApiKeyStore.get_principal_for_key()."""

    def test_returns_none_when_key_not_in_index(self, mock_event_store):
        """Lines 250-251: key not found in index."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        result = store.get_principal_for_key("missing-hash")
        assert result is None

    def test_returns_principal_for_valid_key(self, mock_event_store):
        """Lines 247-276: successful principal lookup."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        creation_event = ApiKeyCreated(
            key_id="kid-gp",
            principal_id="svc-gp",
            key_name="gp-key",
            expires_at=None,
            created_by="admin",
        )
        mock_event_store.list_streams.return_value = ["api_key:gp-hash"]
        mock_event_store.read_stream.return_value = [creation_event]

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        principal = store.get_principal_for_key("gp-hash")

        assert principal is not None
        assert principal.id == PrincipalId("svc-gp")

    def test_raises_revoked_for_revoked_key(self, mock_event_store):
        """Lines 259-260: revoked key raises."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        creation_event = ApiKeyCreated(
            key_id="kid-rr",
            principal_id="svc-rr",
            key_name="rr-key",
            expires_at=None,
            created_by="admin",
        )
        revoke_event = ApiKeyRevoked(
            key_id="kid-rr",
            principal_id="svc-rr",
            revoked_by="admin",
            reason="test",
        )
        mock_event_store.list_streams.return_value = ["api_key:rr-hash"]
        mock_event_store.read_stream.return_value = [creation_event, revoke_event]

        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        with pytest.raises(RevokedCredentialsError):
            store.get_principal_for_key("rr-hash")

    def test_raises_expired_for_expired_key(self, mock_event_store):
        """Lines 262-263: expired key raises."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        past = datetime.now(UTC) - timedelta(hours=1)
        creation_event = ApiKeyCreated(
            key_id="kid-ex",
            principal_id="svc-ex",
            key_name="ex-key",
            expires_at=past.timestamp(),
            created_by="admin",
        )
        mock_event_store.list_streams.return_value = ["api_key:ex-hash"]
        mock_event_store.read_stream.return_value = [creation_event]

        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        with pytest.raises(ExpiredCredentialsError):
            store.get_principal_for_key("ex-hash")

    def test_raises_expired_for_rotated_key_past_grace(self, mock_event_store):
        """Lines 266-271: rotated key past grace period raises."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        creation_event = ApiKeyCreated(
            key_id="kid-rg",
            principal_id="svc-rg",
            key_name="rg-key",
            expires_at=None,
            created_by="admin",
        )
        past_grace = datetime.now(UTC) - timedelta(hours=1)
        rotation_event = KeyRotated(
            key_id="kid-rg",
            principal_id="svc-rg",
            new_key_id="new-kid",
            rotated_at=time.time(),
            grace_until=past_grace.timestamp(),
            rotated_by="admin",
        )
        mock_event_store.list_streams.return_value = ["api_key:rg-hash"]
        mock_event_store.read_stream.return_value = [creation_event, rotation_event]

        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        with pytest.raises(ExpiredCredentialsError, match="rotated"):
            store.get_principal_for_key("rg-hash")

    def test_returns_none_when_load_key_returns_none(self, mock_event_store):
        """Lines 256-257: key in index but load fails returns None."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        # Build index manually
        mock_event_store.list_streams.return_value = []

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        store._index = {"phantom-hash": ("phantom-kid", "svc-phantom")}
        store._principal_index = {"svc-phantom": {"phantom-hash"}}

        # read_stream returns empty -- no events, no snapshot => None
        mock_event_store.read_stream.return_value = []

        result = store.get_principal_for_key("phantom-hash")
        assert result is None


class TestEventSourcedApiKeyStoreCreateKey:
    """Tests for EventSourcedApiKeyStore.create_key()."""

    def test_create_key_returns_raw_key(self, mock_event_store):
        """Lines 289-323: create_key basic path."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        raw_key = store.create_key(
            principal_id="svc-ck",
            name="ck-key",
            created_by="admin",
        )

        assert raw_key.startswith("mcp_")
        mock_event_store.append.assert_called_once()

    def test_create_key_raises_when_max_reached(self, mock_event_store):
        """Lines 292-294: max keys per principal."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        # Manually populate index to simulate many keys
        store._build_index()
        store._principal_index["svc-full"] = set(f"hash-{i}" for i in range(100))

        with pytest.raises(ValueError, match="maximum API keys"):
            store.create_key(principal_id="svc-full", name="overflow")


class TestEventSourcedApiKeyStoreRevokeKey:
    """Tests for EventSourcedApiKeyStore.revoke_key()."""

    def test_revoke_key_success(self, mock_event_store):
        """Lines 333-358: revoke_key finds and revokes."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        creation_event = ApiKeyCreated(
            key_id="kid-rv",
            principal_id="svc-rv",
            key_name="rv-key",
            expires_at=None,
            created_by="admin",
        )
        mock_event_store.list_streams.return_value = ["api_key:rv-hash"]
        mock_event_store.read_stream.return_value = [creation_event]

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        result = store.revoke_key("kid-rv", revoked_by="admin", reason="test")

        assert result is True

    def test_revoke_key_not_found_returns_false(self, mock_event_store):
        """Lines 341-342: key_id not in index."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        result = store.revoke_key("nonexistent")
        assert result is False

    def test_revoke_already_revoked_returns_false(self, mock_event_store):
        """Lines 345: already revoked returns False."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        creation_event = ApiKeyCreated(
            key_id="kid-ar",
            principal_id="svc-ar",
            key_name="ar-key",
            expires_at=None,
            created_by="admin",
        )
        revoke_event = ApiKeyRevoked(
            key_id="kid-ar",
            principal_id="svc-ar",
            revoked_by="admin",
            reason="",
        )
        mock_event_store.list_streams.return_value = ["api_key:ar-hash"]
        mock_event_store.read_stream.return_value = [creation_event, revoke_event]

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        result = store.revoke_key("kid-ar")

        assert result is False


class TestEventSourcedApiKeyStoreListAndCount:
    """Tests for EventSourcedApiKeyStore.list_keys() and count_keys()."""

    def test_list_keys_returns_metadata(self, mock_event_store):
        """Lines 362-382: list_keys for a principal."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        creation_event = ApiKeyCreated(
            key_id="kid-lk",
            principal_id="svc-lk",
            key_name="lk-key",
            expires_at=None,
            created_by="admin",
        )
        mock_event_store.list_streams.return_value = ["api_key:lk-hash"]
        mock_event_store.read_stream.return_value = [creation_event]

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        keys = store.list_keys("svc-lk")

        assert len(keys) == 1
        assert keys[0].key_id == "kid-lk"
        assert isinstance(keys[0], ApiKeyMetadata)

    def test_list_keys_empty_principal(self, mock_event_store):
        """list_keys with no keys for principal."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        keys = store.list_keys("nobody")
        assert keys == []

    def test_count_keys_counts_valid_only(self, mock_event_store):
        """Lines 384-396: count_keys only counts valid keys."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        creation_event = ApiKeyCreated(
            key_id="kid-cnt",
            principal_id="svc-cnt",
            key_name="cnt-key",
            expires_at=None,
            created_by="admin",
        )
        mock_event_store.list_streams.return_value = ["api_key:cnt-hash"]
        mock_event_store.read_stream.return_value = [creation_event]

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        count = store.count_keys("svc-cnt")

        assert count == 1

    def test_count_keys_zero_for_unknown(self, mock_event_store):
        """count_keys returns 0 for unknown principal."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        assert store.count_keys("unknown") == 0


class TestEventSourcedApiKeyStoreRotateKey:
    """Tests for EventSourcedApiKeyStore.rotate_key()."""

    def test_rotate_key_success(self, mock_event_store):
        """Lines 398-470: successful rotation."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        creation_event = ApiKeyCreated(
            key_id="kid-rot",
            principal_id="svc-rot",
            key_name="rot-key",
            expires_at=None,
            created_by="admin",
        )
        mock_event_store.list_streams.return_value = ["api_key:rot-hash"]
        mock_event_store.read_stream.return_value = [creation_event]

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        new_raw = store.rotate_key("kid-rot", grace_period_seconds=3600, rotated_by="admin")

        assert new_raw.startswith("mcp_")
        # append should be called twice: once for old key, once for new key
        assert mock_event_store.append.call_count == 2

    def test_rotate_nonexistent_key_raises(self, mock_event_store):
        """Lines 428-429: rotate unknown key raises."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        with pytest.raises(ValueError, match="not found"):
            store.rotate_key("nonexistent")

    def test_rotate_key_load_fails_raises(self, mock_event_store):
        """Lines 433-434: key in index but load returns None."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        # Manually set up index with a key that can't be loaded
        store._index = {"phantom-hash": ("phantom-kid", "svc-phantom")}
        store._principal_index = {"svc-phantom": {"phantom-hash"}}
        mock_event_store.read_stream.return_value = []

        with pytest.raises(ValueError, match="not found"):
            store.rotate_key("phantom-kid")


# ===========================================================================
# EventSourcedRoleStore tests
# ===========================================================================


class TestEventSourcedRoleStoreGetRole:
    """Tests for EventSourcedRoleStore.get_role()."""

    def test_get_builtin_role(self, mock_event_store):
        """Lines 592-596: get builtin role."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        role = store.get_role("admin")

        assert role is not None
        assert role.name == "admin"

    def test_get_custom_role(self, mock_event_store):
        """Lines 598-599: get custom role."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        custom = Role(name="custom", description="test", permissions=frozenset())
        store.add_role(custom)

        result = store.get_role("custom")
        assert result is not None
        assert result.name == "custom"

    def test_get_nonexistent_role(self, mock_event_store):
        """get_role returns None for unknown role."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        assert store.get_role("phantom") is None


class TestEventSourcedRoleStoreAddRole:
    """Tests for EventSourcedRoleStore.add_role()."""

    def test_add_custom_role(self, mock_event_store):
        """Lines 601-607: add custom role."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        role = Role(name="tester", description="test role", permissions=frozenset())
        store.add_role(role)

        assert store.get_role("tester") is not None

    def test_add_builtin_role_raises(self, mock_event_store):
        """Lines 603-604: cannot override builtin role."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        role = Role(name="admin", description="override", permissions=frozenset())

        with pytest.raises(ValueError, match="built-in"):
            store.add_role(role)


class TestEventSourcedRoleStoreAssignRole:
    """Tests for EventSourcedRoleStore.assign_role()."""

    def test_assign_role_to_principal(self, mock_event_store):
        """Lines 626-648: assign role."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        store.assign_role("svc-1", "admin", scope="global", assigned_by="system")

        mock_event_store.append.assert_called_once()

    def test_assign_unknown_role_raises(self, mock_event_store):
        """Lines 635-636: assign unknown role raises."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)

        with pytest.raises(ValueError, match="Unknown role"):
            store.assign_role("svc-1", "nonexistent")

    def test_assign_already_assigned_is_noop(self, mock_event_store):
        """Duplicate assignment does not save events."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        # First assignment - return events so it sees the role already assigned
        assign_event = RoleAssigned(
            principal_id="svc-dup",
            role_name="admin",
            scope="global",
            assigned_by="system",
        )
        mock_event_store.read_stream.return_value = [assign_event]
        mock_event_store.get_stream_version.return_value = 1

        store = EventSourcedRoleStore(event_store=mock_event_store)
        store.assign_role("svc-dup", "admin", scope="global")

        # append should NOT be called because role is already assigned
        mock_event_store.append.assert_not_called()

    def test_assign_role_publishes_events(self, mock_event_store):
        """EventSourcedRoleStore.assign_role publishes events."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        publisher = Mock()
        store = EventSourcedRoleStore(event_store=mock_event_store, event_publisher=publisher)
        store.assign_role("svc-pub", "admin", scope="global", assigned_by="admin")

        publisher.publish.assert_called_once()
        event = publisher.publish.call_args[0][0]
        assert isinstance(event, RoleAssigned)


class TestEventSourcedRoleStoreRevokeRole:
    """Tests for EventSourcedRoleStore.revoke_role()."""

    def test_revoke_assigned_role(self, mock_event_store):
        """Lines 650-668: revoke role."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        assign_event = RoleAssigned(
            principal_id="svc-rev",
            role_name="admin",
            scope="global",
            assigned_by="system",
        )
        mock_event_store.read_stream.return_value = [assign_event]
        mock_event_store.get_stream_version.return_value = 1

        store = EventSourcedRoleStore(event_store=mock_event_store)
        store.revoke_role("svc-rev", "admin", scope="global", revoked_by="admin")

        mock_event_store.append.assert_called_once()

    def test_revoke_non_assigned_is_noop(self, mock_event_store):
        """Revoking a non-assigned role does not save events."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        mock_event_store.read_stream.return_value = []
        store = EventSourcedRoleStore(event_store=mock_event_store)

        store.revoke_role("svc-no", "admin", scope="global")
        mock_event_store.append.assert_not_called()

    def test_revoke_role_publishes_events(self, mock_event_store):
        """revoke_role publishes events when publisher is set."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        assign_event = RoleAssigned(
            principal_id="svc-rpub",
            role_name="viewer",
            scope="global",
            assigned_by="system",
        )
        mock_event_store.read_stream.return_value = [assign_event]
        mock_event_store.get_stream_version.return_value = 1

        publisher = Mock()
        store = EventSourcedRoleStore(event_store=mock_event_store, event_publisher=publisher)
        store.revoke_role("svc-rpub", "viewer", scope="global", revoked_by="admin")

        publisher.publish.assert_called_once()
        event = publisher.publish.call_args[0][0]
        assert isinstance(event, RoleRevoked)


class TestEventSourcedRoleStoreGetRolesForPrincipal:
    """Tests for EventSourcedRoleStore.get_roles_for_principal()."""

    def test_returns_roles_for_principal(self, mock_event_store):
        """Lines 609-624: get roles for a principal."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        assign_event = RoleAssigned(
            principal_id="svc-grp",
            role_name="admin",
            scope="global",
            assigned_by="system",
        )
        mock_event_store.read_stream.return_value = [assign_event]

        store = EventSourcedRoleStore(event_store=mock_event_store)
        roles = store.get_roles_for_principal("svc-grp")

        assert len(roles) == 1
        assert roles[0].name == "admin"

    def test_returns_empty_for_no_assignments(self, mock_event_store):
        """No assignments returns empty list."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        mock_event_store.read_stream.return_value = []
        store = EventSourcedRoleStore(event_store=mock_event_store)

        roles = store.get_roles_for_principal("nobody")
        assert roles == []


class TestEventSourcedRoleStoreListAllRoles:
    """Tests for EventSourcedRoleStore.list_all_roles()."""

    def test_list_all_custom_roles(self, mock_event_store):
        """Lines 670-673: list custom roles."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        store.add_role(Role(name="custom-a", description="a", permissions=frozenset()))
        store.add_role(Role(name="custom-b", description="b", permissions=frozenset()))

        customs = store.list_all_roles()
        names = {r.name for r in customs}
        assert "custom-a" in names
        assert "custom-b" in names
        assert len(customs) == 2


class TestEventSourcedRoleStoreDeleteRole:
    """Tests for EventSourcedRoleStore.delete_role()."""

    def test_delete_custom_role(self, mock_event_store):
        """Lines 675-693: delete custom role."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        store.add_role(Role(name="to-del", description="del", permissions=frozenset()))
        store.delete_role("to-del")

        assert store.get_role("to-del") is None

    def test_delete_builtin_raises(self, mock_event_store):
        """Lines 687-688: cannot delete builtin."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)

        with pytest.raises(CannotModifyBuiltinRoleError):
            store.delete_role("admin")

    def test_delete_nonexistent_raises(self, mock_event_store):
        """Lines 690-691: role not found."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)

        with pytest.raises(RoleNotFoundError):
            store.delete_role("phantom")


class TestEventSourcedRoleStoreUpdateRole:
    """Tests for EventSourcedRoleStore.update_role()."""

    def test_update_custom_role(self, mock_event_store):
        """Lines 695-728: update custom role."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        store.add_role(Role(name="updatable", description="old", permissions=frozenset()))

        new_perms = [Permission(resource_type="tool", action="write", resource_id="*")]
        updated = store.update_role("updatable", permissions=new_perms, description="new")

        assert updated.description == "new"
        assert len(updated.permissions) == 1

    def test_update_builtin_raises(self, mock_event_store):
        """Lines 717-718: cannot update builtin."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)

        with pytest.raises(CannotModifyBuiltinRoleError):
            store.update_role("admin", permissions=[], description="hack")

    def test_update_nonexistent_raises(self, mock_event_store):
        """Lines 720-721: role not found."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)

        with pytest.raises(RoleNotFoundError):
            store.update_role("phantom", permissions=[], description="")


class TestEventSourcedRoleStoreLoadAssignment:
    """Tests for EventSourcedRoleStore._load_assignment()."""

    def test_load_from_snapshot(self, mock_event_store):
        """Lines 525-526: load from snapshot."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        snapshot = RoleAssignmentSnapshot(
            principal_id="svc-snap",
            assignments={"global": ["admin"]},
            version=5,
        )
        mock_event_store.read_stream.return_value = []

        store = EventSourcedRoleStore(
            event_store=mock_event_store,
            snapshot_store={"svc-snap": snapshot},
        )
        assignment = store._load_assignment("svc-snap")

        assert assignment.has_role("admin")

    def test_load_from_events(self, mock_event_store):
        """Lines 527-528: load from events."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        assign_event = RoleAssigned(
            principal_id="svc-evt",
            role_name="viewer",
            scope="global",
            assigned_by="system",
        )
        mock_event_store.read_stream.return_value = [assign_event]

        store = EventSourcedRoleStore(event_store=mock_event_store)
        assignment = store._load_assignment("svc-evt")

        assert assignment.has_role("viewer")

    def test_load_empty_returns_new_aggregate(self, mock_event_store):
        """Lines 529-530: no snapshot and no events returns fresh aggregate."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        mock_event_store.read_stream.return_value = []
        store = EventSourcedRoleStore(event_store=mock_event_store)

        assignment = store._load_assignment("brand-new")
        assert assignment.principal_id == "brand-new"
        assert assignment.get_role_names() == set()


class TestEventSourcedRoleStorePublishEvents:
    """Tests for EventSourcedRoleStore._publish_events()."""

    def test_publish_with_publisher(self, mock_event_store):
        """Lines 533-536: publish events when publisher is set."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        publisher = Mock()
        store = EventSourcedRoleStore(event_store=mock_event_store, event_publisher=publisher)

        event = RoleAssigned(
            principal_id="svc-pub",
            role_name="admin",
            scope="global",
            assigned_by="system",
        )
        store._publish_events([event])

        publisher.publish.assert_called_once_with(event)

    def test_publish_without_publisher(self, mock_event_store):
        """No error when publisher is None."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        store._publish_events([RoleAssigned(
            principal_id="svc",
            role_name="admin",
            scope="global",
            assigned_by="system",
        )])
