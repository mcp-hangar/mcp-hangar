"""The SQLite-backed API key and role stores."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from mcp_hangar.domain.contracts.authentication import ApiKeyMetadata
from mcp_hangar.domain.events import (
    ApiKeyCreated,
    ApiKeyRevoked,
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
from mcp_hangar.domain.value_objects import Permission, PrincipalId, PrincipalType, Role


@pytest.fixture
def db_path(tmp_path):
    """Return a path to a temporary SQLite database."""
    return tmp_path / "test.db"


@pytest.fixture
def api_key_store(db_path):
    """Create and initialize an SQLiteApiKeyStore."""
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    store = SQLiteApiKeyStore(db_path=db_path)
    store.initialize()
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def api_key_store_with_publisher(db_path):
    """Create SQLiteApiKeyStore with an event publisher."""
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    publisher = Mock()
    store = SQLiteApiKeyStore(db_path=db_path, event_publisher=publisher)
    store.initialize()
    try:
        yield store, publisher
    finally:
        store.close()


@pytest.fixture
def role_store(db_path):
    """Create and initialize an SQLiteRoleStore."""
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteRoleStore

    store = SQLiteRoleStore(db_path=db_path)
    store.initialize()
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def role_store_with_publisher(db_path):
    """Create SQLiteRoleStore with an event publisher."""
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteRoleStore

    publisher = Mock()
    store = SQLiteRoleStore(db_path=db_path, event_publisher=publisher)
    store.initialize()
    try:
        yield store, publisher
    finally:
        store.close()


class TestSQLiteApiKeyStoreInitialize:
    """Tests for SQLiteApiKeyStore.initialize()."""

    def test_initialize_creates_tables(self, db_path):
        from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        store = SQLiteApiKeyStore(db_path=db_path)
        try:
            store.initialize()
            # Verify initialized flag is set
            assert store._initialized is True
        finally:
            store.close()

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

        from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        store = SQLiteApiKeyStore(db_path=db_path)
        store.initialize()
        store.close()

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
        from mcp_hangar.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        principal = api_key_store.get_principal_for_key(key_hash)

        assert principal is not None
        assert principal.id == PrincipalId("svc-test")
        assert principal.type == PrincipalType.SERVICE_ACCOUNT

    def test_raises_revoked_credentials_for_revoked_key(self, api_key_store):
        """Lines 178-182: revoked key raises RevokedCredentialsError."""
        raw_key = api_key_store.create_key(principal_id="svc-revoke", name="rkey")
        from mcp_hangar.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

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
        from mcp_hangar.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

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

        from mcp_hangar.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)

        with pytest.raises(ExpiredCredentialsError, match="rotated"):
            api_key_store.get_principal_for_key(key_hash)

    def test_rotated_key_no_grace_period_rejects_immediately(self, db_path):
        """Lines 196-200: rotated key with no grace_until set rejects immediately."""
        import sqlite3

        from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        store = SQLiteApiKeyStore(db_path=db_path)
        store.initialize()

        raw_key = store.create_key(principal_id="svc-nograce", name="ngkey")
        store.close()
        from mcp_hangar.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

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

        try:
            with pytest.raises(ExpiredCredentialsError, match="rotated"):
                store2.get_principal_for_key(key_hash)
        finally:
            store2.close()

    def test_get_principal_parses_groups_and_metadata(self, db_path):
        """Lines 227-236: groups and metadata parsing."""
        import json
        import sqlite3

        from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        store = SQLiteApiKeyStore(db_path=db_path)
        store.initialize()

        raw_key = store.create_key(
            principal_id="svc-grp",
            name="grp-key",
            groups=frozenset(["admin", "ops"]),
        )
        store.close()
        from mcp_hangar.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

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
        try:
            principal = store2.get_principal_for_key(key_hash)

            assert principal is not None
            assert "admin" in principal.groups
        finally:
            store2.close()
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
        from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        store = SQLiteApiKeyStore(db_path=db_path)
        store.initialize()
        assert store._initialized is True

        store.close()
        assert store._initialized is False

    def test_close_when_no_connection(self, db_path):
        """Close when no connection exists does not raise."""
        from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

        store = SQLiteApiKeyStore(db_path=db_path)
        # Never opened connection
        store.close()
        assert store._initialized is False


class TestSQLiteRoleStoreInitialize:
    """Tests for SQLiteRoleStore.initialize()."""

    def test_initialize_seeds_builtin_roles(self, role_store):
        """Lines 567-595: initialize creates tables and seeds builtin roles."""
        from mcp_hangar.auth.roles import BUILTIN_ROLES

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
            permissions=frozenset(
                [
                    Permission(resource_type="tool", action="invoke", resource_id="*"),
                ]
            ),
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
        from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteRoleStore

        store = SQLiteRoleStore(db_path=db_path)
        store.initialize()
        store.close()
        assert store._initialized is False

    def test_close_when_no_connection(self, db_path):
        """Close without connection does not raise."""
        from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteRoleStore

        store = SQLiteRoleStore(db_path=db_path)
        store.close()
        assert store._initialized is False
