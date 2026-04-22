"""Batch 4: Auth coverage tests for postgres_store, event_sourced_store,
sqlite_tap_store, query handlers, and command handlers.

Covers:
- enterprise/auth/infrastructure/postgres_store.py (214 missed stmts, 0% -> target ~80%)
- enterprise/auth/infrastructure/event_sourced_store.py (47 miss, 83% -> target ~95%)
- enterprise/auth/infrastructure/sqlite_tap_store.py (20 miss -> target 100%)
- enterprise/auth/queries/handlers.py (26 miss -> target ~95%)
- enterprise/auth/commands/handlers.py (20 miss -> target ~95%)
"""

import json
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch, call

import pytest


# ============================================================================
# PostgresApiKeyStore Tests
# ============================================================================


class TestPostgresApiKeyStore:
    """Tests for PostgresApiKeyStore with mock psycopg2 connection factory."""

    def _make_store(self, event_publisher=None, table_prefix=""):
        from enterprise.auth.infrastructure.postgres_store import PostgresApiKeyStore

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)

        @contextmanager
        def connection_factory():
            yield mock_conn

        store = PostgresApiKeyStore(
            connection_factory=connection_factory,
            table_prefix=table_prefix,
            event_publisher=event_publisher,
        )
        return store, mock_conn, mock_cursor

    def test_init_default_table_name(self):
        from enterprise.auth.infrastructure.postgres_store import PostgresApiKeyStore

        store = PostgresApiKeyStore(connection_factory=lambda: None)
        assert store._table == "api_keys"

    def test_init_with_prefix(self):
        from enterprise.auth.infrastructure.postgres_store import PostgresApiKeyStore

        store = PostgresApiKeyStore(connection_factory=lambda: None, table_prefix="auth_")
        assert store._table == "auth_api_keys"

    def test_initialize_creates_schema(self):
        store, mock_conn, mock_cursor = self._make_store()
        store.initialize()
        mock_cursor.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    def test_initialize_with_prefix_replaces_table_name(self):
        store, mock_conn, mock_cursor = self._make_store(table_prefix="myprefix_")
        store.initialize()
        sql = mock_cursor.execute.call_args[0][0]
        assert "myprefix_api_keys" in sql

    def test_get_principal_for_key_not_found_returns_none(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = None
        result = store.get_principal_for_key("abc123hash")
        assert result is None

    def test_get_principal_for_key_revoked_raises(self):
        from mcp_hangar.domain.exceptions import RevokedCredentialsError

        store, mock_conn, mock_cursor = self._make_store()
        # Row: principal_id, tenant_id, groups, name, key_id, expires_at, revoked, metadata, rotated_to_key_id, grace_until
        mock_cursor.fetchone.return_value = (
            "svc-1", "t1", "[]", "mykey", "kid1",
            None, True, {}, None, None,
        )
        with pytest.raises(RevokedCredentialsError):
            store.get_principal_for_key("abc123hash")

    def test_get_principal_for_key_rotated_no_grace_raises(self):
        from mcp_hangar.domain.exceptions import ExpiredCredentialsError

        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (
            "svc-1", "t1", "[]", "mykey", "kid1",
            None, False, {}, "new_kid", None,
        )
        with pytest.raises(ExpiredCredentialsError, match="rotated"):
            store.get_principal_for_key("abc123hash")

    def test_get_principal_for_key_rotated_grace_expired_raises(self):
        from mcp_hangar.domain.exceptions import ExpiredCredentialsError

        store, mock_conn, mock_cursor = self._make_store()
        past_grace = datetime.now(UTC) - timedelta(hours=1)
        mock_cursor.fetchone.return_value = (
            "svc-1", "t1", "[]", "mykey", "kid1",
            None, False, {}, "new_kid", past_grace,
        )
        with pytest.raises(ExpiredCredentialsError, match="rotated"):
            store.get_principal_for_key("abc123hash")

    def test_get_principal_for_key_rotated_in_grace_period_returns_principal(self):
        store, mock_conn, mock_cursor = self._make_store()
        future_grace = datetime.now(UTC) + timedelta(hours=24)
        mock_cursor.fetchone.return_value = (
            "svc-1", "t1", json.dumps(["grp1"]), "mykey", "kid1",
            None, False, {"extra": "val"}, "new_kid", future_grace,
        )
        result = store.get_principal_for_key("abc123hash")
        assert result is not None
        assert str(result.id) == "svc-1"

    def test_get_principal_for_key_expired_raises(self):
        from mcp_hangar.domain.exceptions import ExpiredCredentialsError

        store, mock_conn, mock_cursor = self._make_store()
        past_expiry = datetime.now(UTC) - timedelta(hours=1)
        mock_cursor.fetchone.return_value = (
            "svc-1", "t1", "[]", "mykey", "kid1",
            past_expiry, False, {}, None, None,
        )
        with pytest.raises(ExpiredCredentialsError, match="expired"):
            store.get_principal_for_key("abc123hash")

    def test_get_principal_for_key_valid_returns_principal(self):
        store, mock_conn, mock_cursor = self._make_store()
        future_expiry = datetime.now(UTC) + timedelta(days=30)
        mock_cursor.fetchone.return_value = (
            "svc-1", "t1", json.dumps(["grp1", "grp2"]), "mykey", "kid1",
            future_expiry, False, {"foo": "bar"}, None, None,
        )
        result = store.get_principal_for_key("abc123hash")
        assert result is not None
        assert str(result.id) == "svc-1"
        assert result.tenant_id == "t1"
        assert "grp1" in result.groups
        assert "grp2" in result.groups
        assert result.metadata["key_id"] == "kid1"
        assert result.metadata["foo"] == "bar"

    def test_get_principal_for_key_groups_as_list(self):
        """Groups stored as a native Python list (not JSON string)."""
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (
            "svc-1", None, ["g1", "g2"], "mykey", "kid1",
            None, False, {}, None, None,
        )
        result = store.get_principal_for_key("h1")
        assert "g1" in result.groups
        assert "g2" in result.groups

    def test_get_principal_for_key_last_used_update_failure_does_not_break_auth(self):
        """If updating last_used_at fails, auth should still succeed."""
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (
            "svc-1", None, "[]", "mykey", "kid1",
            None, False, {}, None, None,
        )

        call_count = [0]
        original_execute = mock_cursor.execute

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:  # second execute = last_used_at update
                raise RuntimeError("DB write failed")
            return original_execute(*args, **kwargs)

        mock_cursor.execute = side_effect
        result = store.get_principal_for_key("h1")
        assert result is not None

    def test_create_key_success(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (0,)  # count_keys = 0

        with patch("enterprise.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth_cls:
            # Mock the class-level methods
            mock_auth_cls.generate_key.return_value = "mcp_raw_key_123"
            mock_auth_cls._hash_key.return_value = "hash123"

            # Need to patch import inside method
            with patch(
                "enterprise.auth.infrastructure.postgres_store.secrets.token_urlsafe",
                return_value="keyid_abc",
            ):
                raw_key = store.create_key(
                    principal_id="svc-1",
                    name="test-key",
                    expires_at=datetime(2026, 12, 31, tzinfo=UTC),
                    groups=frozenset(["g1"]),
                    tenant_id="t1",
                    created_by="admin",
                )

        assert raw_key == "mcp_raw_key_123"
        mock_conn.commit.assert_called()

    def test_create_key_max_keys_exceeded_raises(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (100,)  # at max

        with pytest.raises(ValueError, match="maximum API keys"):
            store.create_key(principal_id="svc-1", name="overflow-key")

    def test_create_key_emits_event(self):
        publisher = Mock()
        store, mock_conn, mock_cursor = self._make_store(event_publisher=publisher)
        mock_cursor.fetchone.return_value = (0,)

        with patch("enterprise.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_raw"
            mock_auth._hash_key.return_value = "h"
            with patch("enterprise.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="kid"):
                store.create_key(principal_id="p1", name="k1", created_by="admin")

        publisher.assert_called_once()
        event = publisher.call_args[0][0]
        from mcp_hangar.domain.events import ApiKeyCreated
        assert isinstance(event, ApiKeyCreated)
        assert event.principal_id == "p1"

    def test_create_key_no_event_publisher(self):
        """No event_publisher means no publish call but no error."""
        store, mock_conn, mock_cursor = self._make_store(event_publisher=None)
        mock_cursor.fetchone.return_value = (0,)

        with patch("enterprise.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_raw"
            mock_auth._hash_key.return_value = "h"
            with patch("enterprise.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="kid"):
                raw_key = store.create_key(principal_id="p1", name="k1")
        assert raw_key == "mcp_raw"

    def test_revoke_key_success(self):
        store, mock_conn, mock_cursor = self._make_store()
        # First fetchone returns principal_id row, second returns RETURNING row
        mock_cursor.fetchone.side_effect = [("p1",), ("kid1",)]

        result = store.revoke_key("kid1", revoked_by="admin", reason="compromised")
        assert result is True
        mock_conn.commit.assert_called()

    def test_revoke_key_not_found(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.side_effect = [None, None]

        result = store.revoke_key("nonexistent")
        assert result is False

    def test_revoke_key_emits_event(self):
        publisher = Mock()
        store, mock_conn, mock_cursor = self._make_store(event_publisher=publisher)
        mock_cursor.fetchone.side_effect = [("p1",), ("kid1",)]

        store.revoke_key("kid1", revoked_by="admin", reason="test")
        publisher.assert_called_once()
        from mcp_hangar.domain.events import ApiKeyRevoked
        event = publisher.call_args[0][0]
        assert isinstance(event, ApiKeyRevoked)
        assert event.revoked_by == "admin"

    def test_revoke_key_no_principal_id_skips_event(self):
        """If principal_id not found, event is not published even if revoke succeeds."""
        publisher = Mock()
        store, mock_conn, mock_cursor = self._make_store(event_publisher=publisher)
        mock_cursor.fetchone.side_effect = [None, ("kid1",)]

        result = store.revoke_key("kid1")
        assert result is True
        publisher.assert_not_called()

    def test_list_keys_returns_metadata(self):
        store, mock_conn, mock_cursor = self._make_store()
        now = datetime.now(UTC)
        mock_cursor.fetchall.return_value = [
            ("kid1", "key-1", "p1", now, now + timedelta(days=30), now, False),
            ("kid2", "key-2", "p1", now, None, None, True),
        ]

        keys = store.list_keys("p1")
        assert len(keys) == 2
        assert keys[0].key_id == "kid1"
        assert keys[0].name == "key-1"
        assert keys[1].revoked is True

    def test_list_keys_empty(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchall.return_value = []
        keys = store.list_keys("nobody")
        assert keys == []

    def test_count_keys(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (5,)
        count = store.count_keys("p1")
        assert count == 5

    def test_rotate_key_success(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (
            "oldhash", "p1", "mykey", "t1", "[]", None,
            False, None, None,  # not revoked, not rotated
        )

        with patch("enterprise.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_new_key"
            mock_auth._hash_key.return_value = "newhash"
            with patch("enterprise.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="newkid"):
                raw_key = store.rotate_key("oldkid", grace_period_seconds=3600, rotated_by="admin")

        assert raw_key == "mcp_new_key"
        mock_conn.commit.assert_called()

    def test_rotate_key_not_found_raises(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = None

        with pytest.raises(ValueError, match="not found"):
            store.rotate_key("ghost")

    def test_rotate_key_revoked_raises(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (
            "h", "p1", "k", "t1", "[]", None,
            True, None, None,  # revoked
        )
        with pytest.raises(ValueError, match="revoked"):
            store.rotate_key("kid")

    def test_rotate_key_already_rotated_pending_raises(self):
        store, mock_conn, mock_cursor = self._make_store()
        future = datetime.now(UTC) + timedelta(hours=12)
        mock_cursor.fetchone.return_value = (
            "h", "p1", "k", "t1", "[]", None,
            False, "newkid", future,  # already rotated, grace in future
        )
        with pytest.raises(ValueError, match="pending rotation"):
            store.rotate_key("kid")

    def test_rotate_key_previously_rotated_grace_expired_allows(self):
        """If a previous rotation's grace period expired, re-rotation is allowed."""
        store, mock_conn, mock_cursor = self._make_store()
        past = datetime.now(UTC) - timedelta(hours=1)
        mock_cursor.fetchone.return_value = (
            "h", "p1", "k", "t1", "[]", None,
            False, "oldnew", past,  # grace period expired
        )

        with patch("enterprise.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_regen"
            mock_auth._hash_key.return_value = "newhash"
            with patch("enterprise.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="nkid"):
                raw_key = store.rotate_key("kid")

        assert raw_key == "mcp_regen"

    def test_rotate_key_emits_event(self):
        publisher = Mock()
        store, mock_conn, mock_cursor = self._make_store(event_publisher=publisher)
        mock_cursor.fetchone.return_value = (
            "h", "p1", "k", "t1", "[]", None,
            False, None, None,
        )

        with patch("enterprise.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_raw"
            mock_auth._hash_key.return_value = "nh"
            with patch("enterprise.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="nk"):
                store.rotate_key("kid", rotated_by="admin")

        publisher.assert_called_once()
        from mcp_hangar.domain.events import KeyRotated
        event = publisher.call_args[0][0]
        assert isinstance(event, KeyRotated)
        assert event.rotated_by == "admin"

    def test_rotate_key_db_error_rolls_back(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (
            "h", "p1", "k", "t1", "[]", None,
            False, None, None,
        )

        with patch("enterprise.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_raw"
            mock_auth._hash_key.return_value = "nh"
            with patch("enterprise.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="nk"):
                # Make the INSERT for new key fail
                original_execute = mock_cursor.execute
                call_count = [0]

                def failing_execute(*args, **kwargs):
                    call_count[0] += 1
                    if call_count[0] == 2:  # Second execute = INSERT new key
                        raise RuntimeError("DB error")
                    return original_execute(*args, **kwargs)

                mock_cursor.execute = failing_execute

                with pytest.raises(RuntimeError, match="DB error"):
                    store.rotate_key("kid")

        mock_conn.rollback.assert_called()


# ============================================================================
# PostgresRoleStore Tests
# ============================================================================


class TestPostgresRoleStore:
    """Tests for PostgresRoleStore with mock psycopg2 connection factory."""

    def _make_store(self, event_publisher=None, table_prefix=""):
        from enterprise.auth.infrastructure.postgres_store import PostgresRoleStore

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)

        @contextmanager
        def connection_factory():
            yield mock_conn

        # PostgresRoleStore inherits from IRoleStore (Protocol) which declares
        # delete_role, list_all_roles, update_role as abstract. PostgresRoleStore
        # does not implement them, so clear __abstractmethods__ to allow instantiation.
        PostgresRoleStore.__abstractmethods__ = frozenset()

        store = PostgresRoleStore(
            connection_factory=connection_factory,
            table_prefix=table_prefix,
            event_publisher=event_publisher,
        )
        return store, mock_conn, mock_cursor

    def test_init_default_table_names(self):
        store, _, _ = self._make_store()
        assert store._roles_table == "roles"
        assert store._assignments_table == "role_assignments"

    def test_init_with_prefix(self):
        store, _, _ = self._make_store(table_prefix="auth_")
        assert store._roles_table == "auth_roles"
        assert store._assignments_table == "auth_role_assignments"

    def test_initialize_creates_schema_and_seeds_builtin_roles(self):
        store, mock_conn, mock_cursor = self._make_store()
        store.initialize()
        # Should have called execute at least once for schema + once per builtin role
        assert mock_cursor.execute.call_count >= 2
        mock_conn.commit.assert_called()

    def test_get_role_found(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (
            "viewer",
            "Read-only access",
            json.dumps([{"resource_type": "provider", "action": "read", "resource_id": "*"}]),
        )

        role = store.get_role("viewer")
        assert role is not None
        assert role.name == "viewer"
        assert len(role.permissions) == 1

    def test_get_role_not_found(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = None

        role = store.get_role("nonexistent")
        assert role is None

    def test_get_role_permissions_json_as_string(self):
        store, mock_conn, mock_cursor = self._make_store()
        # permissions_json as a raw string (needs json.loads)
        mock_cursor.fetchone.return_value = (
            "admin", "Full access",
            '[{"resource_type": "*", "action": "*", "resource_id": "*"}]',
        )
        role = store.get_role("admin")
        assert role is not None
        assert len(role.permissions) == 1

    def test_get_role_permissions_json_as_list(self):
        store, mock_conn, mock_cursor = self._make_store()
        # permissions_json already parsed by the DB driver
        mock_cursor.fetchone.return_value = (
            "dev", "Dev access",
            [{"resource_type": "tool", "action": "invoke", "resource_id": "*"}],
        )
        role = store.get_role("dev")
        assert role is not None
        assert len(role.permissions) == 1

    def test_add_role(self):
        from mcp_hangar.domain.value_objects import Permission, Role

        store, mock_conn, mock_cursor = self._make_store()
        role = Role(
            name="custom-role",
            permissions=frozenset([Permission(resource_type="tool", action="invoke", resource_id="*")]),
            description="Custom role",
        )
        store.add_role(role)
        mock_cursor.execute.assert_called_once()
        mock_conn.commit.assert_called()

    def test_get_roles_for_principal_all_scopes(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchall.return_value = [
            ("viewer", "Read only", json.dumps([{"resource_type": "provider", "action": "read", "resource_id": "*"}])),
        ]

        roles = store.get_roles_for_principal("p1", scope="*")
        assert len(roles) == 1
        assert roles[0].name == "viewer"

    def test_get_roles_for_principal_specific_scope(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchall.return_value = [
            ("admin", "Full", [{"resource_type": "*", "action": "*", "resource_id": "*"}]),
        ]

        roles = store.get_roles_for_principal("p1", scope="tenant:xyz")
        assert len(roles) == 1

    def test_get_roles_for_principal_permissions_json_string_parsing(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchall.return_value = [
            ("r1", "Desc", '[{"resource_type": "a", "action": "b"}]'),
        ]
        roles = store.get_roles_for_principal("p1")
        assert len(roles) == 1
        perm = list(roles[0].permissions)[0]
        assert perm.resource_type == "a"
        assert perm.resource_id == "*"  # default

    def test_assign_role_success(self):
        publisher = Mock()
        store, mock_conn, mock_cursor = self._make_store(event_publisher=publisher)
        mock_cursor.fetchone.side_effect = [(1,), (42,)]  # role exists, RETURNING id

        store.assign_role("p1", "viewer", scope="global", assigned_by="admin")
        mock_conn.commit.assert_called()
        publisher.assert_called_once()
        from mcp_hangar.domain.events import RoleAssigned
        event = publisher.call_args[0][0]
        assert isinstance(event, RoleAssigned)

    def test_assign_role_unknown_role_raises(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = None  # role not found

        with pytest.raises(ValueError, match="Unknown role"):
            store.assign_role("p1", "ghost-role")

    def test_assign_role_already_assigned_no_event(self):
        publisher = Mock()
        store, mock_conn, mock_cursor = self._make_store(event_publisher=publisher)
        mock_cursor.fetchone.side_effect = [(1,), None]  # role exists, ON CONFLICT DO NOTHING

        store.assign_role("p1", "viewer")
        publisher.assert_not_called()

    def test_revoke_role_success(self):
        publisher = Mock()
        store, mock_conn, mock_cursor = self._make_store(event_publisher=publisher)
        mock_cursor.fetchone.return_value = (1,)  # RETURNING id

        store.revoke_role("p1", "viewer", scope="global", revoked_by="admin")
        mock_conn.commit.assert_called()
        publisher.assert_called_once()
        from mcp_hangar.domain.events import RoleRevoked
        event = publisher.call_args[0][0]
        assert isinstance(event, RoleRevoked)

    def test_revoke_role_not_assigned_no_event(self):
        publisher = Mock()
        store, mock_conn, mock_cursor = self._make_store(event_publisher=publisher)
        mock_cursor.fetchone.return_value = None

        store.revoke_role("p1", "viewer")
        publisher.assert_not_called()


# ============================================================================
# create_postgres_connection_factory Tests
# ============================================================================


class TestCreatePostgresConnectionFactory:
    """Tests for create_postgres_connection_factory."""

    def test_missing_psycopg2_raises_import_error(self):
        from enterprise.auth.infrastructure.postgres_store import create_postgres_connection_factory

        with patch.dict("sys.modules", {"psycopg2": None, "psycopg2.pool": None}):
            with pytest.raises(ImportError, match="psycopg2"):
                create_postgres_connection_factory()

    def test_factory_creates_pool_and_returns_callable(self):
        mock_pool_module = MagicMock()
        mock_pool = MagicMock()
        mock_pool_module.ThreadedConnectionPool.return_value = mock_pool

        mock_psycopg2 = MagicMock()
        mock_psycopg2.pool = mock_pool_module

        import sys
        with patch.dict(sys.modules, {"psycopg2": mock_psycopg2, "psycopg2.pool": mock_pool_module}):
            from enterprise.auth.infrastructure.postgres_store import create_postgres_connection_factory

            factory = create_postgres_connection_factory(
                host="db.local", port=5433, database="test_db",
                user="testuser", password="secret",
                min_connections=1, max_connections=5,
            )

            assert callable(factory)


# ============================================================================
# EventSourcedApiKeyStore - gap coverage tests
# ============================================================================


class TestEventSourcedApiKeyStoreGaps:
    """Tests for uncovered paths in EventSourcedApiKeyStore."""

    def _make_store(self, events=None, streams=None, publisher=None, snapshot_store=None):
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        event_store = Mock(spec=["read_stream", "append", "list_streams", "get_stream_version"])
        event_store.list_streams.return_value = streams or []
        event_store.read_stream.return_value = iter(events or [])
        event_store.append.return_value = 1

        store = EventSourcedApiKeyStore(
            event_store=event_store,
            event_publisher=publisher,
            snapshot_store=snapshot_store,
        )
        return store, event_store

    def test_build_index_scans_streams(self):
        from mcp_hangar.domain.events import ApiKeyCreated

        creation_event = ApiKeyCreated(
            key_id="kid1", principal_id="p1", key_name="k1",
            expires_at=None, created_by="admin",
        )

        event_store = Mock()
        event_store.list_streams.return_value = ["api_key:hash1"]
        event_store.read_stream.return_value = iter([creation_event])

        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore
        store = EventSourcedApiKeyStore(event_store=event_store)
        store._build_index()

        assert "hash1" in store._index
        assert store._index["hash1"] == ("kid1", "p1")
        assert "hash1" in store._principal_index["p1"]

    def test_build_index_called_once(self):
        store, event_store = self._make_store(streams=[])
        store._build_index()
        store._build_index()  # second call should be no-op
        event_store.list_streams.assert_called_once()

    def test_rotate_key_not_found_raises(self):
        store, event_store = self._make_store(streams=[])
        store._build_index()
        store._index = {}  # empty

        with pytest.raises(ValueError, match="not found"):
            store.rotate_key("ghost")

    def test_rotate_key_load_returns_none_raises(self):
        """rotate_key raises ValueError if _load_key returns None for found index entry."""
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        event_store = Mock()
        event_store.list_streams.return_value = []
        event_store.read_stream.return_value = iter([])
        event_store.append.return_value = 1

        store = EventSourcedApiKeyStore(event_store=event_store)
        store._index = {"h1": ("kid1", "p1")}
        store._principal_index = {"p1": {"h1"}}

        with pytest.raises(ValueError, match="not found"):
            store.rotate_key("kid1")

    def test_rotate_key_success_creates_new_key_and_rotates_old(self):
        from mcp_hangar.domain.events import ApiKeyCreated
        from mcp_hangar.domain.model.event_sourced_api_key import EventSourcedApiKey

        creation_event = ApiKeyCreated(
            key_id="kid1", principal_id="p1", key_name="test",
            expires_at=None, created_by="admin",
        )

        event_store = Mock()
        event_store.list_streams.return_value = ["api_key:h1"]

        # read_stream called multiple times during rotate_key
        event_store.read_stream.return_value = iter([creation_event])
        event_store.append.return_value = 2

        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore
        store = EventSourcedApiKeyStore(event_store=event_store)
        store._index = {"h1": ("kid1", "p1")}
        store._principal_index = {"p1": {"h1"}}

        # Patch _load_key to return a proper EventSourcedApiKey
        original_key = EventSourcedApiKey.create(
            key_hash="h1", key_id="kid1", principal_id="p1",
            name="test", created_by="admin",
        )
        original_key.collect_events()  # clear events

        with patch.object(store, "_load_key", return_value=original_key):
            raw_key = store.rotate_key("kid1", grace_period_seconds=3600, rotated_by="admin")

        assert raw_key.startswith("mcp_")

    def test_maybe_create_snapshot_below_threshold(self):
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        store = EventSourcedApiKeyStore(event_store=Mock())
        store._maybe_create_snapshot("k1", 10, lambda: "snap")
        assert "k1" not in store._snapshot_store

    def test_maybe_create_snapshot_at_threshold(self):
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        store = EventSourcedApiKeyStore(event_store=Mock())
        create_fn = Mock(return_value="snapshot_data")
        store._maybe_create_snapshot("k1", 50, create_fn)
        assert store._snapshot_store["k1"] == "snapshot_data"
        create_fn.assert_called_once()

    def test_maybe_create_snapshot_existing_snapshot_not_enough_events(self):
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore
        from mcp_hangar.domain.model.event_sourced_api_key import ApiKeySnapshot

        existing = Mock()
        existing.version = 45
        store = EventSourcedApiKeyStore(event_store=Mock(), snapshot_store={"k1": existing})
        create_fn = Mock()
        store._maybe_create_snapshot("k1", 60, create_fn)
        create_fn.assert_not_called()  # only 15 events since last snapshot

    def test_load_key_with_snapshot(self):
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore
        from mcp_hangar.domain.model.event_sourced_api_key import ApiKeySnapshot

        snapshot = ApiKeySnapshot(
            key_hash="h1", key_id="kid1", principal_id="p1",
            name="test", tenant_id=None, groups=[],
            created_at=datetime.now(UTC).timestamp(),
            expires_at=None, last_used_at=None,
            revoked=False, revoked_at=None,
            rotated_to_key_id=None, grace_until=None,
            version=5,
        )

        event_store = Mock()
        event_store.read_stream.return_value = iter([])

        store = EventSourcedApiKeyStore(
            event_store=event_store,
            snapshot_store={"h1": snapshot},
        )

        key = store._load_key("h1", index_entry=("kid1", "p1"))
        assert key is not None
        assert key.key_id == "kid1"


# ============================================================================
# EventSourcedRoleStore - gap coverage tests
# ============================================================================


class TestEventSourcedRoleStoreGaps:
    """Tests for uncovered paths in EventSourcedRoleStore."""

    def _make_store(self, publisher=None):
        from enterprise.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        event_store = Mock()
        event_store.read_stream.return_value = iter([])
        event_store.get_stream_version.return_value = 0
        event_store.append.return_value = 1

        store = EventSourcedRoleStore(
            event_store=event_store,
            event_publisher=publisher,
        )
        return store, event_store

    def test_delete_role_builtin_raises(self):
        from mcp_hangar.domain.exceptions import CannotModifyBuiltinRoleError
        from enterprise.auth.roles import BUILTIN_ROLES

        store, _ = self._make_store()
        builtin_name = next(iter(BUILTIN_ROLES))

        with pytest.raises(CannotModifyBuiltinRoleError):
            store.delete_role(builtin_name)

    def test_delete_role_not_found_raises(self):
        from mcp_hangar.domain.exceptions import RoleNotFoundError

        store, _ = self._make_store()

        with pytest.raises(RoleNotFoundError):
            store.delete_role("nonexistent-custom")

    def test_delete_role_success(self):
        from mcp_hangar.domain.value_objects import Permission, Role

        store, _ = self._make_store()
        role = Role(name="temp-role", permissions=frozenset(), description="Temporary")
        store.add_role(role)

        store.delete_role("temp-role")
        assert store.get_role("temp-role") is None

    def test_update_role_builtin_raises(self):
        from mcp_hangar.domain.exceptions import CannotModifyBuiltinRoleError
        from enterprise.auth.roles import BUILTIN_ROLES

        store, _ = self._make_store()
        builtin_name = next(iter(BUILTIN_ROLES))

        with pytest.raises(CannotModifyBuiltinRoleError):
            store.update_role(builtin_name, [], "new desc")

    def test_update_role_not_found_raises(self):
        from mcp_hangar.domain.exceptions import RoleNotFoundError

        store, _ = self._make_store()

        with pytest.raises(RoleNotFoundError):
            store.update_role("ghost", [], "desc")

    def test_update_role_success(self):
        from mcp_hangar.domain.value_objects import Permission, Role

        store, _ = self._make_store()
        store.add_role(Role(name="my-role", permissions=frozenset(), description="old"))

        new_perms = [Permission(resource_type="tool", action="invoke", resource_id="*")]
        updated = store.update_role("my-role", new_perms, "new desc")

        assert updated.name == "my-role"
        assert updated.description == "new desc"
        assert len(updated.permissions) == 1

    def test_update_role_none_description(self):
        from mcp_hangar.domain.value_objects import Role

        store, _ = self._make_store()
        store.add_role(Role(name="my-role", permissions=frozenset(), description="old"))

        updated = store.update_role("my-role", [], None)
        assert updated.description == ""

    def test_list_all_roles_returns_custom_roles(self):
        from mcp_hangar.domain.value_objects import Role

        store, _ = self._make_store()
        store.add_role(Role(name="role-a", permissions=frozenset(), description="A"))
        store.add_role(Role(name="role-b", permissions=frozenset(), description="B"))

        roles = store.list_all_roles()
        names = {r.name for r in roles}
        assert "role-a" in names
        assert "role-b" in names

    def test_add_role_builtin_raises(self):
        from enterprise.auth.roles import BUILTIN_ROLES
        from mcp_hangar.domain.value_objects import Role

        store, _ = self._make_store()
        builtin_name = next(iter(BUILTIN_ROLES))

        with pytest.raises(ValueError, match="built-in"):
            store.add_role(Role(name=builtin_name, permissions=frozenset()))

    def test_get_role_builtin(self):
        from enterprise.auth.roles import BUILTIN_ROLES

        store, _ = self._make_store()
        builtin_name = next(iter(BUILTIN_ROLES))
        role = store.get_role(builtin_name)
        assert role is not None
        assert role.name == builtin_name

    def test_get_role_custom(self):
        from mcp_hangar.domain.value_objects import Role

        store, _ = self._make_store()
        store.add_role(Role(name="my-custom", permissions=frozenset(), description="C"))
        role = store.get_role("my-custom")
        assert role is not None

    def test_get_role_not_found(self):
        store, _ = self._make_store()
        assert store.get_role("nope") is None

    def test_maybe_create_snapshot_for_role_store(self):
        """EventSourcedRoleStore has its own _maybe_create_snapshot."""
        store, _ = self._make_store()
        create_fn = Mock(return_value="role_snap")
        store._maybe_create_snapshot("p1", 50, create_fn)
        assert store._snapshot_store["p1"] == "role_snap"


# ============================================================================
# SQLiteToolAccessPolicyStore Tests
# ============================================================================


class TestSQLiteToolAccessPolicyStore:
    """Tests for SQLiteToolAccessPolicyStore with real SQLite :memory: or tmp file."""

    def _make_store(self):
        from enterprise.auth.infrastructure.sqlite_tap_store import SQLiteToolAccessPolicyStore

        tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmpfile.close()
        store = SQLiteToolAccessPolicyStore(db_path=tmpfile.name)
        return store, tmpfile.name

    def test_init_creates_schema(self):
        store, path = self._make_store()
        conn = sqlite3.connect(path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tool_access_policies'")
        assert cursor.fetchone() is not None
        conn.close()
        store.close()

    def test_set_and_get_policy(self):
        store, _ = self._make_store()
        store.set_policy("provider", "math", allow_list=["add", "sub"], deny_list=["delete"])

        policy = store.get_policy("provider", "math")
        assert policy is not None
        assert policy.allow_list == ("add", "sub")
        assert policy.deny_list == ("delete",)
        store.close()

    def test_get_policy_not_found(self):
        store, _ = self._make_store()
        policy = store.get_policy("provider", "ghost")
        assert policy is None
        store.close()

    def test_set_policy_upsert(self):
        store, _ = self._make_store()
        store.set_policy("provider", "math", allow_list=["add"], deny_list=[])
        store.set_policy("provider", "math", allow_list=["add", "mul"], deny_list=["rm"])

        policy = store.get_policy("provider", "math")
        assert policy.allow_list == ("add", "mul")
        assert policy.deny_list == ("rm",)
        store.close()

    def test_clear_policy(self):
        store, _ = self._make_store()
        store.set_policy("provider", "math", allow_list=["add"], deny_list=[])
        store.clear_policy("provider", "math")

        policy = store.get_policy("provider", "math")
        assert policy is None
        store.close()

    def test_clear_policy_nonexistent_no_error(self):
        store, _ = self._make_store()
        store.clear_policy("provider", "ghost")  # should not raise
        store.close()

    def test_list_all_policies(self):
        store, _ = self._make_store()
        store.set_policy("provider", "math", allow_list=["add"], deny_list=[])
        store.set_policy("group", "grp1", allow_list=[], deny_list=["rm"])
        store.set_policy("member", "g1:m1", allow_list=["x"], deny_list=["y"])

        all_policies = store.list_all_policies()
        assert len(all_policies) == 3

        scopes = {p[0] for p in all_policies}
        assert scopes == {"provider", "group", "member"}
        store.close()

    def test_list_all_policies_empty(self):
        store, _ = self._make_store()
        all_policies = store.list_all_policies()
        assert all_policies == []
        store.close()

    def test_close_checkpoints_and_closes(self):
        store, path = self._make_store()
        store.set_policy("provider", "x", allow_list=[], deny_list=[])
        store.close()

        # After close, connection should be None
        assert store._local.connection is None

    def test_close_when_no_connection(self):
        store, _ = self._make_store()
        store.close()
        # Second close should be a no-op
        store.close()

    def test_close_checkpoint_failure_does_not_raise(self):
        """If WAL checkpoint fails, close should still complete."""
        store, _ = self._make_store()
        conn = store._get_connection()

        # sqlite3.Connection.execute is read-only, so we wrap the connection
        # with a Mock that delegates most calls but raises on checkpoint SQL.
        mock_conn = MagicMock(wraps=conn)
        original_execute = conn.execute

        def fail_checkpoint(sql, *args):
            if "wal_checkpoint" in str(sql).lower():
                raise sqlite3.OperationalError("checkpoint failed")
            return original_execute(sql, *args)

        mock_conn.execute = fail_checkpoint
        store._local.connection = mock_conn
        store.close()  # should not raise
        assert store._local.connection is None

    def test_thread_local_connections(self):
        """Each thread gets its own connection."""
        store, path = self._make_store()
        connections = []

        def get_conn():
            conn = store._get_connection()
            connections.append(id(conn))

        t1 = threading.Thread(target=get_conn)
        t2 = threading.Thread(target=get_conn)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Different threads should get different connection objects
        assert len(connections) == 2
        # (They may or may not be different ids depending on thread reuse,
        #  but the thread-local mechanism should provide isolation)
        store.close()


# ============================================================================
# Query Handler Tests
# ============================================================================


class TestGetApiKeysByPrincipalHandler:
    """Tests for GetApiKeysByPrincipalHandler."""

    def test_handle_returns_keys_with_metadata(self):
        from enterprise.auth.queries.handlers import GetApiKeysByPrincipalHandler
        from enterprise.auth.queries.queries import GetApiKeysByPrincipalQuery
        from mcp_hangar.domain.contracts.authentication import ApiKeyMetadata

        now = datetime.now(UTC)
        mock_store = Mock()
        mock_store.list_keys.return_value = [
            ApiKeyMetadata(key_id="k1", name="key-1", principal_id="p1", created_at=now, revoked=False),
            ApiKeyMetadata(key_id="k2", name="key-2", principal_id="p1", created_at=now, revoked=True),
        ]

        handler = GetApiKeysByPrincipalHandler(mock_store)
        result = handler.handle(GetApiKeysByPrincipalQuery(principal_id="p1", include_revoked=True))

        assert result["total"] == 2
        assert result["active"] == 1
        assert len(result["keys"]) == 2

    def test_handle_excludes_revoked(self):
        from enterprise.auth.queries.handlers import GetApiKeysByPrincipalHandler
        from enterprise.auth.queries.queries import GetApiKeysByPrincipalQuery
        from mcp_hangar.domain.contracts.authentication import ApiKeyMetadata

        now = datetime.now(UTC)
        mock_store = Mock()
        mock_store.list_keys.return_value = [
            ApiKeyMetadata(key_id="k1", name="key-1", principal_id="p1", created_at=now, revoked=False),
            ApiKeyMetadata(key_id="k2", name="key-2", principal_id="p1", created_at=now, revoked=True),
        ]

        handler = GetApiKeysByPrincipalHandler(mock_store)
        result = handler.handle(GetApiKeysByPrincipalQuery(principal_id="p1", include_revoked=False))

        assert result["total"] == 1
        assert len(result["keys"]) == 1
        assert result["keys"][0]["key_id"] == "k1"


class TestGetApiKeyCountHandler:
    """Tests for GetApiKeyCountHandler."""

    def test_handle_returns_count(self):
        from enterprise.auth.queries.handlers import GetApiKeyCountHandler
        from enterprise.auth.queries.queries import GetApiKeyCountQuery

        mock_store = Mock()
        mock_store.count_keys.return_value = 3

        handler = GetApiKeyCountHandler(mock_store)
        result = handler.handle(GetApiKeyCountQuery(principal_id="p1"))

        assert result["active_keys"] == 3
        assert result["principal_id"] == "p1"


class TestGetRolesForPrincipalHandler:
    """Tests for GetRolesForPrincipalHandler."""

    def test_handle_returns_roles(self):
        from enterprise.auth.queries.handlers import GetRolesForPrincipalHandler
        from enterprise.auth.queries.queries import GetRolesForPrincipalQuery
        from mcp_hangar.domain.value_objects import Permission, Role

        mock_store = Mock()
        mock_store.get_roles_for_principal.return_value = [
            Role(name="viewer", permissions=frozenset([Permission(resource_type="provider", action="read")])),
        ]

        handler = GetRolesForPrincipalHandler(mock_store)
        result = handler.handle(GetRolesForPrincipalQuery(principal_id="p1", scope="global"))

        assert result["count"] == 1
        assert result["roles"][0]["name"] == "viewer"


class TestGetRoleHandler:
    """Tests for GetRoleHandler."""

    def test_handle_role_found(self):
        from enterprise.auth.queries.handlers import GetRoleHandler
        from enterprise.auth.queries.queries import GetRoleQuery
        from mcp_hangar.domain.value_objects import Permission, Role

        mock_store = Mock()
        mock_store.get_role.return_value = Role(
            name="admin",
            permissions=frozenset([Permission(resource_type="*", action="*")]),
            description="Full access",
        )

        handler = GetRoleHandler(mock_store)
        result = handler.handle(GetRoleQuery(role_name="admin"))

        assert result["found"] is True
        assert result["role"]["name"] == "admin"
        assert result["role"]["permissions_count"] == 1

    def test_handle_role_not_found(self):
        from enterprise.auth.queries.handlers import GetRoleHandler
        from enterprise.auth.queries.queries import GetRoleQuery

        mock_store = Mock()
        mock_store.get_role.return_value = None

        handler = GetRoleHandler(mock_store)
        result = handler.handle(GetRoleQuery(role_name="ghost"))

        assert result["found"] is False
        assert result["role"] is None


class TestListBuiltinRolesHandler:
    """Tests for ListBuiltinRolesHandler."""

    def test_handle_returns_builtin_roles(self):
        from enterprise.auth.queries.handlers import ListBuiltinRolesHandler
        from enterprise.auth.queries.queries import ListBuiltinRolesQuery
        from enterprise.auth.roles import BUILTIN_ROLES

        handler = ListBuiltinRolesHandler()
        result = handler.handle(ListBuiltinRolesQuery())

        assert result["count"] == len(BUILTIN_ROLES)
        assert len(result["roles"]) == len(BUILTIN_ROLES)


class TestCheckPermissionHandler:
    """Tests for CheckPermissionHandler."""

    def test_permission_granted(self):
        from enterprise.auth.queries.handlers import CheckPermissionHandler
        from enterprise.auth.queries.queries import CheckPermissionQuery
        from mcp_hangar.domain.value_objects import Permission, Role

        mock_store = Mock()
        mock_store.get_roles_for_principal.return_value = [
            Role(name="admin", permissions=frozenset([Permission(resource_type="*", action="*")])),
        ]

        handler = CheckPermissionHandler(mock_store)
        result = handler.handle(CheckPermissionQuery(
            principal_id="p1", action="read", resource_type="provider",
        ))

        assert result["allowed"] is True
        assert result["granted_by_role"] == "admin"

    def test_permission_denied(self):
        from enterprise.auth.queries.handlers import CheckPermissionHandler
        from enterprise.auth.queries.queries import CheckPermissionQuery
        from mcp_hangar.domain.value_objects import Permission, Role

        mock_store = Mock()
        mock_store.get_roles_for_principal.return_value = [
            Role(name="viewer", permissions=frozenset([Permission(resource_type="provider", action="read")])),
        ]

        handler = CheckPermissionHandler(mock_store)
        result = handler.handle(CheckPermissionQuery(
            principal_id="p1", action="delete", resource_type="provider",
        ))

        assert result["allowed"] is False
        assert result["granted_by_role"] is None


class TestListAllRolesHandler:
    """Tests for ListAllRolesHandler."""

    def test_handle_with_builtin(self):
        from enterprise.auth.queries.handlers import ListAllRolesHandler
        from enterprise.auth.queries.queries import ListAllRolesQuery
        from enterprise.auth.roles import BUILTIN_ROLES
        from mcp_hangar.domain.value_objects import Permission, Role

        mock_store = Mock()
        mock_store.list_all_roles.return_value = [
            Role(name="custom-a", permissions=frozenset(), description="A"),
        ]

        handler = ListAllRolesHandler(mock_store)
        result = handler.handle(ListAllRolesQuery(include_builtin=True))

        assert result["builtin_count"] == len(BUILTIN_ROLES)
        assert result["custom_count"] == 1
        assert result["total"] == len(BUILTIN_ROLES) + 1

    def test_handle_without_builtin(self):
        from enterprise.auth.queries.handlers import ListAllRolesHandler
        from enterprise.auth.queries.queries import ListAllRolesQuery
        from mcp_hangar.domain.value_objects import Role

        mock_store = Mock()
        mock_store.list_all_roles.return_value = [
            Role(name="custom-a", permissions=frozenset(), description="A"),
        ]

        handler = ListAllRolesHandler(mock_store)
        result = handler.handle(ListAllRolesQuery(include_builtin=False))

        assert result["builtin_count"] == 0
        assert result["custom_count"] == 1


class TestListPrincipalsHandler:
    """Tests for ListPrincipalsHandler."""

    def test_handle_with_list_principals_method(self):
        from enterprise.auth.queries.handlers import ListPrincipalsHandler
        from enterprise.auth.queries.queries import ListPrincipalsQuery

        mock_store = Mock()
        mock_store.list_principals.return_value = [
            {"principal_id": "p1", "roles": ["admin"]},
            {"principal_id": "p2", "roles": ["viewer"]},
        ]

        handler = ListPrincipalsHandler(mock_store)
        result = handler.handle(ListPrincipalsQuery())

        assert result["total"] == 2

    def test_handle_with_assignments_dict_fallback(self):
        from enterprise.auth.queries.handlers import ListPrincipalsHandler
        from enterprise.auth.queries.queries import ListPrincipalsQuery

        mock_store = Mock(spec=[])  # no list_principals
        mock_store._assignments = {
            "p1": {"global": {"admin", "viewer"}, "tenant:x": {"developer"}},
            "p2": {"global": {"viewer"}},
        }

        handler = ListPrincipalsHandler(mock_store)
        result = handler.handle(ListPrincipalsQuery())

        assert result["total"] == 2
        principals_ids = {p["principal_id"] for p in result["principals"]}
        assert principals_ids == {"p1", "p2"}

    def test_handle_no_method_no_assignments(self):
        from enterprise.auth.queries.handlers import ListPrincipalsHandler
        from enterprise.auth.queries.queries import ListPrincipalsQuery

        mock_store = Mock(spec=[])  # no list_principals, no _assignments

        handler = ListPrincipalsHandler(mock_store)
        result = handler.handle(ListPrincipalsQuery())

        assert result["total"] == 0
        assert result["principals"] == []


class TestGetToolAccessPolicyHandler:
    """Tests for GetToolAccessPolicyHandler."""

    def test_handle_policy_found(self):
        from enterprise.auth.queries.handlers import GetToolAccessPolicyHandler
        from enterprise.auth.queries.queries import GetToolAccessPolicyQuery
        from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

        mock_store = Mock()
        mock_store.get_policy.return_value = ToolAccessPolicy(
            allow_list=("add", "sub"),
            deny_list=("rm",),
        )

        handler = GetToolAccessPolicyHandler(mock_store)
        result = handler.handle(GetToolAccessPolicyQuery(scope="provider", target_id="math"))

        assert result["found"] is True
        assert result["allow_list"] == ["add", "sub"]
        assert result["deny_list"] == ["rm"]

    def test_handle_policy_not_found(self):
        from enterprise.auth.queries.handlers import GetToolAccessPolicyHandler
        from enterprise.auth.queries.queries import GetToolAccessPolicyQuery

        mock_store = Mock()
        mock_store.get_policy.return_value = None

        handler = GetToolAccessPolicyHandler(mock_store)
        result = handler.handle(GetToolAccessPolicyQuery(scope="provider", target_id="ghost"))

        assert result["found"] is False
        assert result["allow_list"] == []
        assert result["deny_list"] == []


class TestRegisterAuthQueryHandlers:
    """Tests for register_auth_query_handlers function."""

    def test_register_all_handlers(self):
        from enterprise.auth.queries.handlers import register_auth_query_handlers
        from enterprise.auth.queries.queries import (
            CheckPermissionQuery,
            GetApiKeyCountQuery,
            GetApiKeysByPrincipalQuery,
            GetRoleQuery,
            GetRolesForPrincipalQuery,
            GetToolAccessPolicyQuery,
            ListAllRolesQuery,
            ListBuiltinRolesQuery,
            ListPrincipalsQuery,
        )

        mock_bus = Mock()
        mock_api_key_store = Mock()
        mock_role_store = Mock()
        mock_tap_store = Mock()

        register_auth_query_handlers(
            mock_bus,
            api_key_store=mock_api_key_store,
            role_store=mock_role_store,
            tap_store=mock_tap_store,
        )

        # Check register was called for each query type
        registered_types = {c[0][0] for c in mock_bus.register.call_args_list}
        assert GetApiKeysByPrincipalQuery in registered_types
        assert GetApiKeyCountQuery in registered_types
        assert GetRolesForPrincipalQuery in registered_types
        assert GetRoleQuery in registered_types
        assert CheckPermissionQuery in registered_types
        assert ListAllRolesQuery in registered_types
        assert ListPrincipalsQuery in registered_types
        assert ListBuiltinRolesQuery in registered_types
        assert GetToolAccessPolicyQuery in registered_types

    def test_register_with_none_stores(self):
        from enterprise.auth.queries.handlers import register_auth_query_handlers
        from enterprise.auth.queries.queries import ListBuiltinRolesQuery

        mock_bus = Mock()
        register_auth_query_handlers(mock_bus)

        # Only ListBuiltinRolesQuery should be registered (no store needed)
        registered_types = {c[0][0] for c in mock_bus.register.call_args_list}
        assert ListBuiltinRolesQuery in registered_types
        assert len(registered_types) == 1


# ============================================================================
# Command Handler Tests
# ============================================================================


class TestCreateApiKeyHandler:
    """Tests for CreateApiKeyHandler."""

    def test_handle_creates_key(self):
        from enterprise.auth.commands.handlers import CreateApiKeyHandler
        from enterprise.auth.commands.commands import CreateApiKeyCommand
        from mcp_hangar.domain.contracts.authentication import ApiKeyMetadata

        now = datetime.now(UTC)
        mock_store = Mock()
        mock_store.create_key.return_value = "mcp_raw_key"
        mock_store.list_keys.return_value = [
            ApiKeyMetadata(key_id="kid1", name="test-key", principal_id="p1", created_at=now),
        ]

        handler = CreateApiKeyHandler(mock_store)
        result = handler.handle(CreateApiKeyCommand(
            principal_id="p1", name="test-key", created_by="admin",
        ))

        assert result["raw_key"] == "mcp_raw_key"
        assert result["key_id"] == "kid1"
        assert result["principal_id"] == "p1"
        assert "warning" in result

    def test_handle_key_metadata_not_found(self):
        from enterprise.auth.commands.handlers import CreateApiKeyHandler
        from enterprise.auth.commands.commands import CreateApiKeyCommand

        mock_store = Mock()
        mock_store.create_key.return_value = "mcp_raw"
        mock_store.list_keys.return_value = []

        handler = CreateApiKeyHandler(mock_store)
        result = handler.handle(CreateApiKeyCommand(principal_id="p1", name="k1"))

        assert result["key_id"] is None
        assert result["raw_key"] == "mcp_raw"


class TestRevokeApiKeyHandler:
    """Tests for RevokeApiKeyHandler."""

    def test_handle_revokes(self):
        from enterprise.auth.commands.handlers import RevokeApiKeyHandler
        from enterprise.auth.commands.commands import RevokeApiKeyCommand

        mock_store = Mock()
        mock_store.revoke_key.return_value = True

        handler = RevokeApiKeyHandler(mock_store)
        result = handler.handle(RevokeApiKeyCommand(
            key_id="kid1", revoked_by="admin", reason="compromised",
        ))

        assert result["revoked"] is True
        assert result["revoked_by"] == "admin"

    def test_handle_revoke_fails(self):
        from enterprise.auth.commands.handlers import RevokeApiKeyHandler
        from enterprise.auth.commands.commands import RevokeApiKeyCommand

        mock_store = Mock()
        mock_store.revoke_key.return_value = False

        handler = RevokeApiKeyHandler(mock_store)
        result = handler.handle(RevokeApiKeyCommand(key_id="ghost"))

        assert result["revoked"] is False


class TestListApiKeysHandler:
    """Tests for ListApiKeysHandler."""

    def test_handle_lists_keys(self):
        from enterprise.auth.commands.handlers import ListApiKeysHandler
        from enterprise.auth.commands.commands import ListApiKeysCommand
        from mcp_hangar.domain.contracts.authentication import ApiKeyMetadata

        now = datetime.now(UTC)
        mock_store = Mock()
        mock_store.list_keys.return_value = [
            ApiKeyMetadata(key_id="k1", name="n1", principal_id="p1", created_at=now),
        ]

        handler = ListApiKeysHandler(mock_store)
        result = handler.handle(ListApiKeysCommand(principal_id="p1"))

        assert result["count"] == 1
        assert result["keys"][0]["key_id"] == "k1"


class TestAssignRoleHandler:
    """Tests for AssignRoleHandler."""

    def test_handle_assigns(self):
        from enterprise.auth.commands.handlers import AssignRoleHandler
        from enterprise.auth.commands.commands import AssignRoleCommand

        mock_store = Mock()
        handler = AssignRoleHandler(mock_store)
        result = handler.handle(AssignRoleCommand(
            principal_id="p1", role_name="viewer", scope="global", assigned_by="admin",
        ))

        assert result["assigned"] is True
        mock_store.assign_role.assert_called_once()


class TestRevokeRoleHandler:
    """Tests for RevokeRoleHandler."""

    def test_handle_revokes(self):
        from enterprise.auth.commands.handlers import RevokeRoleHandler
        from enterprise.auth.commands.commands import RevokeRoleCommand

        mock_store = Mock()
        handler = RevokeRoleHandler(mock_store)
        result = handler.handle(RevokeRoleCommand(
            principal_id="p1", role_name="viewer", scope="global", revoked_by="admin",
        ))

        assert result["revoked"] is True
        mock_store.revoke_role.assert_called_once()


class TestCreateCustomRoleHandler:
    """Tests for CreateCustomRoleHandler."""

    def test_handle_creates_role(self):
        from enterprise.auth.commands.handlers import CreateCustomRoleHandler
        from enterprise.auth.commands.commands import CreateCustomRoleCommand

        mock_store = Mock()
        mock_event_bus = Mock()

        handler = CreateCustomRoleHandler(mock_store, event_bus=mock_event_bus)
        result = handler.handle(CreateCustomRoleCommand(
            role_name="custom-role",
            description="Custom",
            permissions=frozenset(["mcp_server:read", "tool:invoke"]),
            created_by="admin",
        ))

        assert result["created"] is True
        assert result["permissions_count"] == 2
        mock_store.add_role.assert_called_once()
        mock_event_bus.publish.assert_called_once()

    def test_handle_no_event_bus(self):
        from enterprise.auth.commands.handlers import CreateCustomRoleHandler
        from enterprise.auth.commands.commands import CreateCustomRoleCommand

        mock_store = Mock()
        handler = CreateCustomRoleHandler(mock_store, event_bus=None)
        result = handler.handle(CreateCustomRoleCommand(
            role_name="custom-role", permissions=frozenset(["mcp_server:read"]),
        ))
        assert result["created"] is True


class TestDeleteCustomRoleHandler:
    """Tests for DeleteCustomRoleHandler."""

    def test_handle_deletes_role(self):
        from enterprise.auth.commands.handlers import DeleteCustomRoleHandler
        from enterprise.auth.commands.commands import DeleteCustomRoleCommand

        mock_store = Mock()
        mock_event_bus = Mock()

        handler = DeleteCustomRoleHandler(mock_store, event_bus=mock_event_bus)
        result = handler.handle(DeleteCustomRoleCommand(
            role_name="my-role", deleted_by="admin",
        ))

        assert result["deleted"] is True
        mock_store.delete_role.assert_called_once_with("my-role")
        mock_event_bus.publish.assert_called_once()

    def test_handle_builtin_role_propagates_error(self):
        from enterprise.auth.commands.handlers import DeleteCustomRoleHandler
        from enterprise.auth.commands.commands import DeleteCustomRoleCommand
        from mcp_hangar.domain.exceptions import CannotModifyBuiltinRoleError

        mock_store = Mock()
        mock_store.delete_role.side_effect = CannotModifyBuiltinRoleError("admin")

        handler = DeleteCustomRoleHandler(mock_store, event_bus=Mock())

        with pytest.raises(CannotModifyBuiltinRoleError):
            handler.handle(DeleteCustomRoleCommand(role_name="admin"))


class TestUpdateCustomRoleHandler:
    """Tests for UpdateCustomRoleHandler."""

    def test_handle_updates_role(self):
        from enterprise.auth.commands.handlers import UpdateCustomRoleHandler
        from enterprise.auth.commands.commands import UpdateCustomRoleCommand
        from mcp_hangar.domain.value_objects import Permission, Role

        mock_store = Mock()
        mock_store.update_role.return_value = Role(
            name="my-role",
            permissions=frozenset([Permission(resource_type="tool", action="invoke")]),
            description="Updated",
        )
        mock_event_bus = Mock()

        handler = UpdateCustomRoleHandler(mock_store, event_bus=mock_event_bus)
        result = handler.handle(UpdateCustomRoleCommand(
            role_name="my-role",
            permissions=["tool:invoke"],
            description="Updated",
            updated_by="admin",
        ))

        assert result["updated"] is True
        assert result["permissions_count"] == 1
        mock_event_bus.publish.assert_called_once()

    def test_handle_role_not_found_propagates(self):
        from enterprise.auth.commands.handlers import UpdateCustomRoleHandler
        from enterprise.auth.commands.commands import UpdateCustomRoleCommand
        from mcp_hangar.domain.exceptions import RoleNotFoundError

        mock_store = Mock()
        mock_store.update_role.side_effect = RoleNotFoundError("ghost")

        handler = UpdateCustomRoleHandler(mock_store, event_bus=Mock())

        with pytest.raises(RoleNotFoundError):
            handler.handle(UpdateCustomRoleCommand(role_name="ghost"))


class TestSetToolAccessPolicyHandler:
    """Tests for SetToolAccessPolicyHandler."""

    def test_handle_provider_scope(self):
        from enterprise.auth.commands.handlers import SetToolAccessPolicyHandler
        from enterprise.auth.commands.commands import SetToolAccessPolicyCommand

        mock_tap_store = Mock()
        mock_event_bus = Mock()

        handler = SetToolAccessPolicyHandler(mock_tap_store, event_bus=mock_event_bus)

        mock_resolver = Mock()
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            result = handler.handle(SetToolAccessPolicyCommand(
                scope="provider", target_id="math",
                allow_list=["add"], deny_list=["rm"],
            ))

        assert result["set"] is True
        mock_tap_store.set_policy.assert_called_once()
        mock_resolver.set_mcp_server_policy.assert_called_once()
        mock_event_bus.publish.assert_called_once()

    def test_handle_group_scope(self):
        from enterprise.auth.commands.handlers import SetToolAccessPolicyHandler
        from enterprise.auth.commands.commands import SetToolAccessPolicyCommand

        mock_tap_store = Mock()
        mock_event_bus = Mock()

        handler = SetToolAccessPolicyHandler(mock_tap_store, event_bus=mock_event_bus)

        mock_resolver = Mock()
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            result = handler.handle(SetToolAccessPolicyCommand(
                scope="group", target_id="grp1",
                allow_list=[], deny_list=["x"],
            ))

        mock_resolver.set_group_policy.assert_called_once()

    def test_handle_member_scope_with_colon_format(self):
        from enterprise.auth.commands.handlers import SetToolAccessPolicyHandler
        from enterprise.auth.commands.commands import SetToolAccessPolicyCommand

        mock_tap_store = Mock()
        mock_event_bus = Mock()

        handler = SetToolAccessPolicyHandler(mock_tap_store, event_bus=mock_event_bus)

        mock_resolver = Mock()
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            result = handler.handle(SetToolAccessPolicyCommand(
                scope="member", target_id="grp1:member1",
                allow_list=["add"], deny_list=[],
            ))

        mock_resolver.set_member_policy.assert_called_once_with("grp1", "member1", mock_resolver.set_member_policy.call_args[0][2])

    def test_handle_member_scope_without_colon(self):
        from enterprise.auth.commands.handlers import SetToolAccessPolicyHandler
        from enterprise.auth.commands.commands import SetToolAccessPolicyCommand

        mock_tap_store = Mock()
        mock_event_bus = Mock()

        handler = SetToolAccessPolicyHandler(mock_tap_store, event_bus=mock_event_bus)

        mock_resolver = Mock()
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            handler.handle(SetToolAccessPolicyCommand(
                scope="member", target_id="single_id",
                allow_list=[], deny_list=[],
            ))

        mock_resolver.set_member_policy.assert_called_once_with("single_id", "single_id", mock_resolver.set_member_policy.call_args[0][2])


class TestClearToolAccessPolicyHandler:
    """Tests for ClearToolAccessPolicyHandler."""

    def test_handle_clears_policy(self):
        from enterprise.auth.commands.handlers import ClearToolAccessPolicyHandler
        from enterprise.auth.commands.commands import ClearToolAccessPolicyCommand

        mock_tap_store = Mock()
        mock_event_bus = Mock()

        handler = ClearToolAccessPolicyHandler(mock_tap_store, event_bus=mock_event_bus)
        result = handler.handle(ClearToolAccessPolicyCommand(scope="provider", target_id="math"))

        assert result["cleared"] is True
        mock_tap_store.clear_policy.assert_called_once_with(scope="provider", target_id="math")
        mock_event_bus.publish.assert_called_once()


class TestRegisterAuthCommandHandlers:
    """Tests for register_auth_command_handlers function."""

    def test_register_all_handlers(self):
        from enterprise.auth.commands.handlers import register_auth_command_handlers
        from enterprise.auth.commands.commands import (
            AssignRoleCommand,
            ClearToolAccessPolicyCommand,
            CreateApiKeyCommand,
            CreateCustomRoleCommand,
            DeleteCustomRoleCommand,
            ListApiKeysCommand,
            RevokeApiKeyCommand,
            RevokeRoleCommand,
            SetToolAccessPolicyCommand,
            UpdateCustomRoleCommand,
        )

        mock_bus = Mock()
        register_auth_command_handlers(
            mock_bus,
            api_key_store=Mock(),
            role_store=Mock(),
            tap_store=Mock(),
            event_bus=Mock(),
        )

        registered_types = {c[0][0] for c in mock_bus.register.call_args_list}
        assert CreateApiKeyCommand in registered_types
        assert RevokeApiKeyCommand in registered_types
        assert ListApiKeysCommand in registered_types
        assert AssignRoleCommand in registered_types
        assert RevokeRoleCommand in registered_types
        assert CreateCustomRoleCommand in registered_types
        assert DeleteCustomRoleCommand in registered_types
        assert UpdateCustomRoleCommand in registered_types
        assert SetToolAccessPolicyCommand in registered_types
        assert ClearToolAccessPolicyCommand in registered_types

    def test_register_with_none_stores(self):
        from enterprise.auth.commands.handlers import register_auth_command_handlers

        mock_bus = Mock()
        register_auth_command_handlers(mock_bus)

        # No handlers should be registered when all stores are None
        mock_bus.register.assert_not_called()

    def test_register_only_api_key_store(self):
        from enterprise.auth.commands.handlers import register_auth_command_handlers
        from enterprise.auth.commands.commands import CreateApiKeyCommand, RevokeApiKeyCommand, ListApiKeysCommand

        mock_bus = Mock()
        register_auth_command_handlers(mock_bus, api_key_store=Mock())

        registered_types = {c[0][0] for c in mock_bus.register.call_args_list}
        assert CreateApiKeyCommand in registered_types
        assert RevokeApiKeyCommand in registered_types
        assert ListApiKeysCommand in registered_types
        assert len(registered_types) == 3

    def test_register_only_role_store(self):
        from enterprise.auth.commands.handlers import register_auth_command_handlers
        from enterprise.auth.commands.commands import (
            AssignRoleCommand,
            CreateCustomRoleCommand,
            DeleteCustomRoleCommand,
            RevokeRoleCommand,
            UpdateCustomRoleCommand,
        )

        mock_bus = Mock()
        register_auth_command_handlers(mock_bus, role_store=Mock(), event_bus=Mock())

        registered_types = {c[0][0] for c in mock_bus.register.call_args_list}
        assert AssignRoleCommand in registered_types
        assert RevokeRoleCommand in registered_types
        assert CreateCustomRoleCommand in registered_types
        assert DeleteCustomRoleCommand in registered_types
        assert UpdateCustomRoleCommand in registered_types
        assert len(registered_types) == 5
