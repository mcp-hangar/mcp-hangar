"""The PostgreSQL-backed API key and role stores, and the connection factory they share."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest


class _NullFactory:
    """A factory that yields nothing, for tests that never touch a connection."""

    @contextmanager
    def get_connection(self):
        yield None


class TestPostgresApiKeyStore:
    """Tests for PostgresApiKeyStore with mock psycopg2 connection factory."""

    def _make_store(self, event_publisher=None, table_prefix=""):
        from mcp_hangar.auth.infrastructure.postgres_store import PostgresApiKeyStore

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)

        # The port, not a bare callable: the store depends on
        # `IConnectionFactory`, so the double has to be one too -- otherwise the
        # test passes against a shape production does not use.
        class _Factory:
            @contextmanager
            def get_connection(self):
                yield mock_conn

        connection_factory = _Factory()

        store = PostgresApiKeyStore(
            connection_factory=connection_factory,
            table_prefix=table_prefix,
            event_publisher=event_publisher,
        )
        return store, mock_conn, mock_cursor

    def test_init_default_table_name(self):
        from mcp_hangar.auth.infrastructure.postgres_store import PostgresApiKeyStore

        store = PostgresApiKeyStore(connection_factory=_NullFactory())
        assert store._table == "api_keys"

    def test_init_with_prefix(self):
        from mcp_hangar.auth.infrastructure.postgres_store import PostgresApiKeyStore

        store = PostgresApiKeyStore(connection_factory=_NullFactory(), table_prefix="auth_")
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
        # Row: principal_id, tenant_id, groups, name, key_id, expires_at,
        # revoked, metadata, rotated_to_key_id, grace_until
        mock_cursor.fetchone.return_value = (
            "svc-1",
            "t1",
            "[]",
            "mykey",
            "kid1",
            None,
            True,
            {},
            None,
            None,
        )
        with pytest.raises(RevokedCredentialsError):
            store.get_principal_for_key("abc123hash")

    def test_get_principal_for_key_rotated_no_grace_raises(self):
        from mcp_hangar.domain.exceptions import ExpiredCredentialsError

        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (
            "svc-1",
            "t1",
            "[]",
            "mykey",
            "kid1",
            None,
            False,
            {},
            "new_kid",
            None,
        )
        with pytest.raises(ExpiredCredentialsError, match="rotated"):
            store.get_principal_for_key("abc123hash")

    def test_get_principal_for_key_rotated_grace_expired_raises(self):
        from mcp_hangar.domain.exceptions import ExpiredCredentialsError

        store, mock_conn, mock_cursor = self._make_store()
        past_grace = datetime.now(UTC) - timedelta(hours=1)
        mock_cursor.fetchone.return_value = (
            "svc-1",
            "t1",
            "[]",
            "mykey",
            "kid1",
            None,
            False,
            {},
            "new_kid",
            past_grace,
        )
        with pytest.raises(ExpiredCredentialsError, match="rotated"):
            store.get_principal_for_key("abc123hash")

    def test_get_principal_for_key_rotated_in_grace_period_returns_principal(self):
        store, mock_conn, mock_cursor = self._make_store()
        future_grace = datetime.now(UTC) + timedelta(hours=24)
        mock_cursor.fetchone.return_value = (
            "svc-1",
            "t1",
            json.dumps(["grp1"]),
            "mykey",
            "kid1",
            None,
            False,
            {"extra": "val"},
            "new_kid",
            future_grace,
        )
        result = store.get_principal_for_key("abc123hash")
        assert result is not None
        assert str(result.id) == "svc-1"

    def test_get_principal_for_key_expired_raises(self):
        from mcp_hangar.domain.exceptions import ExpiredCredentialsError

        store, mock_conn, mock_cursor = self._make_store()
        past_expiry = datetime.now(UTC) - timedelta(hours=1)
        mock_cursor.fetchone.return_value = (
            "svc-1",
            "t1",
            "[]",
            "mykey",
            "kid1",
            past_expiry,
            False,
            {},
            None,
            None,
        )
        with pytest.raises(ExpiredCredentialsError, match="expired"):
            store.get_principal_for_key("abc123hash")

    def test_get_principal_for_key_valid_returns_principal(self):
        store, mock_conn, mock_cursor = self._make_store()
        future_expiry = datetime.now(UTC) + timedelta(days=30)
        mock_cursor.fetchone.return_value = (
            "svc-1",
            "t1",
            json.dumps(["grp1", "grp2"]),
            "mykey",
            "kid1",
            future_expiry,
            False,
            {"foo": "bar"},
            None,
            None,
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
            "svc-1",
            None,
            ["g1", "g2"],
            "mykey",
            "kid1",
            None,
            False,
            {},
            None,
            None,
        )
        result = store.get_principal_for_key("h1")
        assert "g1" in result.groups
        assert "g2" in result.groups

    def test_get_principal_for_key_last_used_update_failure_does_not_break_auth(self):
        """If updating last_used_at fails, auth should still succeed."""
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (
            "svc-1",
            None,
            "[]",
            "mykey",
            "kid1",
            None,
            False,
            {},
            None,
            None,
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

        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth_cls:
            # Mock the class-level methods
            mock_auth_cls.generate_key.return_value = "mcp_raw_key_123"
            mock_auth_cls._hash_key.return_value = "hash123"

            # Need to patch import inside method
            with patch(
                "mcp_hangar.auth.infrastructure.postgres_store.secrets.token_urlsafe",
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

        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_raw"
            mock_auth._hash_key.return_value = "h"
            with patch("mcp_hangar.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="kid"):
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

        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_raw"
            mock_auth._hash_key.return_value = "h"
            with patch("mcp_hangar.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="kid"):
                raw_key = store.create_key(principal_id="p1", name="k1")
        assert raw_key == "mcp_raw"

    def test_bootstrap_initial_admin_commits_metadata_only(self):
        publisher = Mock()
        store, mock_conn, mock_cursor = self._make_store(event_publisher=publisher)
        mock_cursor.fetchone.side_effect = [(True,), (1,)]

        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_raw_key"
            mock_auth._hash_key.return_value = "key_hash"
            with patch("mcp_hangar.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="key_id"):
                result = store.bootstrap_initial_admin("service:bootstrap", "initial admin")

        assert result == ("mcp_raw_key", "key_id")
        mock_conn.commit.assert_called_once()
        assert [type(call.args[0]).__name__ for call in publisher.call_args_list] == ["ApiKeyCreated", "RoleAssigned"]
        assert all("mcp_raw_key" not in repr(call.args[0]) for call in publisher.call_args_list)

    def test_bootstrap_initial_admin_loser_rolls_back_without_events(self):
        publisher = Mock()
        store, mock_conn, mock_cursor = self._make_store(event_publisher=publisher)
        mock_cursor.fetchone.return_value = None

        assert store.bootstrap_initial_admin("service:bootstrap", "initial admin") is None
        mock_conn.rollback.assert_called_once()
        publisher.assert_not_called()

    def test_bootstrap_initial_admin_failure_rolls_back_claim(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.side_effect = [(True,), None]

        with pytest.raises(ValueError, match="admin role"):
            store.bootstrap_initial_admin("service:bootstrap", "initial admin")

        mock_conn.rollback.assert_called_once()

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
            "oldhash",
            "p1",
            "mykey",
            "t1",
            "[]",
            None,
            False,
            None,
            None,  # not revoked, not rotated
        )

        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_new_key"
            mock_auth._hash_key.return_value = "newhash"
            with patch("mcp_hangar.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="newkid"):
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
            "h",
            "p1",
            "k",
            "t1",
            "[]",
            None,
            True,
            None,
            None,  # revoked
        )
        with pytest.raises(ValueError, match="revoked"):
            store.rotate_key("kid")

    def test_rotate_key_already_rotated_pending_raises(self):
        store, mock_conn, mock_cursor = self._make_store()
        future = datetime.now(UTC) + timedelta(hours=12)
        mock_cursor.fetchone.return_value = (
            "h",
            "p1",
            "k",
            "t1",
            "[]",
            None,
            False,
            "newkid",
            future,  # already rotated, grace in future
        )
        with pytest.raises(ValueError, match="pending rotation"):
            store.rotate_key("kid")

    def test_rotate_key_previously_rotated_grace_expired_allows(self):
        """If a previous rotation's grace period expired, re-rotation is allowed."""
        store, mock_conn, mock_cursor = self._make_store()
        past = datetime.now(UTC) - timedelta(hours=1)
        mock_cursor.fetchone.return_value = (
            "h",
            "p1",
            "k",
            "t1",
            "[]",
            None,
            False,
            "oldnew",
            past,  # grace period expired
        )

        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_regen"
            mock_auth._hash_key.return_value = "newhash"
            with patch("mcp_hangar.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="nkid"):
                raw_key = store.rotate_key("kid")

        assert raw_key == "mcp_regen"

    def test_rotate_key_emits_event(self):
        publisher = Mock()
        store, mock_conn, mock_cursor = self._make_store(event_publisher=publisher)
        mock_cursor.fetchone.return_value = (
            "h",
            "p1",
            "k",
            "t1",
            "[]",
            None,
            False,
            None,
            None,
        )

        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_raw"
            mock_auth._hash_key.return_value = "nh"
            with patch("mcp_hangar.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="nk"):
                store.rotate_key("kid", rotated_by="admin")

        publisher.assert_called_once()
        from mcp_hangar.domain.events import KeyRotated

        event = publisher.call_args[0][0]
        assert isinstance(event, KeyRotated)
        assert event.rotated_by == "admin"

    def test_rotate_key_db_error_rolls_back(self):
        store, mock_conn, mock_cursor = self._make_store()
        mock_cursor.fetchone.return_value = (
            "h",
            "p1",
            "k",
            "t1",
            "[]",
            None,
            False,
            None,
            None,
        )

        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.ApiKeyAuthenticator") as mock_auth:
            mock_auth.generate_key.return_value = "mcp_raw"
            mock_auth._hash_key.return_value = "nh"
            with patch("mcp_hangar.auth.infrastructure.postgres_store.secrets.token_urlsafe", return_value="nk"):
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


class TestPostgresRoleStore:
    """Tests for PostgresRoleStore with mock psycopg2 connection factory."""

    def _make_store(self, event_publisher=None, table_prefix=""):
        from mcp_hangar.auth.infrastructure.postgres_store import PostgresRoleStore

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)

        # The port, not a bare callable: the store depends on
        # `IConnectionFactory`, so the double has to be one too -- otherwise the
        # test passes against a shape production does not use.
        class _Factory:
            @contextmanager
            def get_connection(self):
                yield mock_conn

        connection_factory = _Factory()

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
            "admin",
            "Full access",
            '[{"resource_type": "*", "action": "*", "resource_id": "*"}]',
        )
        role = store.get_role("admin")
        assert role is not None
        assert len(role.permissions) == 1

    def test_get_role_permissions_json_as_list(self):
        store, mock_conn, mock_cursor = self._make_store()
        # permissions_json already parsed by the DB driver
        mock_cursor.fetchone.return_value = (
            "dev",
            "Dev access",
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


class TestPostgresConnectionFactory:
    """The single factory. There used to be two identical ones (#779)."""

    def test_missing_psycopg2_names_what_to_install(self):
        """The message is the whole value here.

        The `postgres` extra used to install asyncpg while this code imports
        psycopg2, so an operator following the documented install hit this error
        and needed it to name the right package (#779).
        """
        from mcp_hangar.infrastructure.persistence.database_common import (
            PostgresConfig,
            PostgresConnectionFactory,
        )

        factory = PostgresConnectionFactory(PostgresConfig())

        with patch.dict("sys.modules", {"psycopg2": None, "psycopg2.pool": None}):
            with pytest.raises(ImportError, match="psycopg2-binary"):
                with factory.get_connection():
                    pass

    def test_the_pool_is_built_from_the_config(self):
        from mcp_hangar.infrastructure.persistence.database_common import (
            PostgresConfig,
            PostgresConnectionFactory,
        )

        mock_pool_module = MagicMock()
        mock_psycopg2 = MagicMock()
        mock_psycopg2.pool = mock_pool_module

        import sys

        with patch.dict(sys.modules, {"psycopg2": mock_psycopg2, "psycopg2.pool": mock_pool_module}):
            factory = PostgresConnectionFactory(
                PostgresConfig(
                    host="db.local",
                    port=5433,
                    database="test_db",
                    user="testuser",
                    password="secret",
                    min_connections=1,
                    max_connections=5,
                )
            )
            with factory.get_connection():
                pass

        kwargs = mock_pool_module.ThreadedConnectionPool.call_args.kwargs
        assert kwargs["host"] == "db.local"
        assert kwargs["port"] == 5433
        assert (kwargs["minconn"], kwargs["maxconn"]) == (1, 5)
