"""Tests for transactional durable initial-admin bootstrap."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from mcp_hangar.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator
from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore, SQLiteRoleStore
from mcp_hangar.domain.contracts.authentication import AuthRequest
from mcp_hangar.domain.events import ApiKeyCreated, RoleAssigned


@pytest.fixture
def sqlite_stores(tmp_path):
    """Initialize the durable API-key and role stores against one database."""
    db_path = tmp_path / "auth.db"
    key_store = SQLiteApiKeyStore(db_path)
    role_store = SQLiteRoleStore(db_path)
    key_store.initialize()
    role_store.initialize()
    yield db_path, key_store, role_store
    key_store.close()
    role_store.close()


def test_sqlite_bootstrap_creates_admin_key_and_assignment(sqlite_stores):
    db_path, key_store, role_store = sqlite_stores

    result = key_store.bootstrap_initial_admin("service:bootstrap", "initial admin")

    assert result is not None
    raw_key, key_id = result
    assert key_store.list_keys("service:bootstrap")[0].key_id == key_id
    assert {role.name for role in role_store.get_roles_for_principal("service:bootstrap")} == {"admin"}

    conn = key_store._get_connection()
    stored_hash = conn.execute("SELECT key_hash FROM api_keys WHERE key_id = ?", (key_id,)).fetchone()["key_hash"]
    assert stored_hash == ApiKeyAuthenticator._hash_key(raw_key)
    assert stored_hash != raw_key

    principal = ApiKeyAuthenticator(key_store).authenticate(
        AuthRequest(headers={"X-API-Key": raw_key}, source_ip="127.0.0.1")
    )
    assert principal.id.value == "service:bootstrap"

    restarted_store = SQLiteApiKeyStore(db_path)
    restarted_store.initialize()
    assert restarted_store.bootstrap_initial_admin("service:other", "other admin") is None


def test_reinitializing_role_store_preserves_admin_assignment(sqlite_stores):
    """Re-seeding built-in roles on restart must not cascade-wipe assignments.

    Regression: ``initialize()`` seeded built-in roles with ``INSERT OR REPLACE``,
    which deletes the existing row on conflict; ``role_assignments.role_name`` has
    ``ON DELETE CASCADE``, so every restart / ``bootstrap_auth`` call silently
    dropped the bootstrapped admin's assignment. The seed now upserts in place.
    """
    db_path, key_store, role_store = sqlite_stores

    assert key_store.bootstrap_initial_admin("service:bootstrap", "initial admin") is not None
    assert {r.name for r in role_store.get_roles_for_principal("service:bootstrap")} == {"admin"}

    # Simulate a restart: a fresh role store re-runs initialize() (re-seeds the
    # built-in roles, including "admin"). The prior assignment must survive.
    restarted_role_store = SQLiteRoleStore(db_path)
    restarted_role_store.initialize()
    try:
        assert {r.name for r in restarted_role_store.get_roles_for_principal("service:bootstrap")} == {"admin"}
    finally:
        restarted_role_store.close()


def test_sqlite_bootstrap_has_exactly_one_concurrent_winner(sqlite_stores):
    db_path, _, _ = sqlite_stores

    def bootstrap(principal_id):
        store = SQLiteApiKeyStore(db_path)
        store.initialize()
        try:
            return store.bootstrap_initial_admin(principal_id, "initial admin")
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(bootstrap, ["service:one", "service:two"]))

    assert sum(result is not None for result in results) == 1


def test_sqlite_bootstrap_rolls_back_claim_key_and_assignment_on_failure(sqlite_stores):
    _, key_store, _ = sqlite_stores
    conn = key_store._get_connection()
    conn.execute(
        """
        CREATE TRIGGER fail_initial_admin_assignment
        BEFORE INSERT ON role_assignments
        BEGIN
            SELECT RAISE(ABORT, 'assignment failure');
        END
        """
    )
    conn.commit()

    with pytest.raises(Exception, match="assignment failure"):
        key_store.bootstrap_initial_admin("service:bootstrap", "initial admin")

    assert conn.execute("SELECT COUNT(*) FROM initial_admin_bootstrap").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM role_assignments").fetchone()[0] == 0


def test_sqlite_bootstrap_emits_metadata_events_only(tmp_path):
    publisher = Mock()
    db_path = tmp_path / "auth.db"
    key_store = SQLiteApiKeyStore(db_path, event_publisher=publisher)
    key_store.initialize()
    role_store = SQLiteRoleStore(db_path)
    role_store.initialize()

    raw_key, _ = key_store.bootstrap_initial_admin("service:bootstrap", "initial admin")

    assert [type(call.args[0]) for call in publisher.call_args_list] == [ApiKeyCreated, RoleAssigned]
    assert all(raw_key not in repr(call.args[0]) for call in publisher.call_args_list)
    key_store.close()
    role_store.close()
