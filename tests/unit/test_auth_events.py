"""Tests for auth storage CQRS integration - event emission."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from unittest.mock import Mock

import pytest

from mcp_hangar.domain.events import ApiKeyCreated, ApiKeyRevoked, RoleAssigned, RoleRevoked
from mcp_hangar.infrastructure.auth.sqlite_store import SQLiteApiKeyStore, SQLiteRoleStore


class TestSQLiteApiKeyStoreEvents:
    """Test that SQLiteApiKeyStore emits domain events."""

    @pytest.fixture
    def event_publisher(self):
        """Create mock event publisher."""
        return Mock()

    @pytest.fixture
    def store(self, event_publisher):
        """Create store with event publisher."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteApiKeyStore(db_path, event_publisher=event_publisher)
            store.initialize()
            yield store
            store.close()

    def test_create_key_emits_event(self, store, event_publisher):
        """Creating a key emits ApiKeyCreated event."""
        store.create_key(
            principal_id="test-user",
            name="Test Key",
            created_by="admin",
        )

        event_publisher.assert_called_once()
        event = event_publisher.call_args[0][0]

        assert isinstance(event, ApiKeyCreated)
        assert event.principal_id == "test-user"
        assert event.key_name == "Test Key"
        assert event.created_by == "admin"

    def test_create_key_with_expiration_emits_event(self, store, event_publisher):
        """Creating a key with expiration includes timestamp in event."""
        expires_at = datetime.now(timezone.utc) + timedelta(days=30)

        store.create_key(
            principal_id="test-user",
            name="Expiring Key",
            expires_at=expires_at,
            created_by="admin",
        )

        event = event_publisher.call_args[0][0]
        assert event.expires_at is not None
        assert abs(event.expires_at - expires_at.timestamp()) < 1

    def test_revoke_key_emits_event(self, store, event_publisher):
        """Revoking a key emits ApiKeyRevoked event."""
        # Create key first
        store.create_key(principal_id="test-user", name="To Revoke")
        event_publisher.reset_mock()

        # Get key_id
        keys = store.list_keys("test-user")
        key_id = keys[0].key_id

        # Revoke
        result = store.revoke_key(key_id, revoked_by="admin", reason="Testing")

        assert result is True
        event_publisher.assert_called_once()
        event = event_publisher.call_args[0][0]

        assert isinstance(event, ApiKeyRevoked)
        assert event.key_id == key_id
        assert event.principal_id == "test-user"
        assert event.revoked_by == "admin"
        assert event.reason == "Testing"

    def test_revoke_nonexistent_key_no_event(self, store, event_publisher):
        """Revoking nonexistent key does not emit event."""
        result = store.revoke_key("nonexistent", revoked_by="admin")

        assert result is False
        event_publisher.assert_not_called()

    def test_store_without_publisher_works(self):
        """Store works without event publisher."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteApiKeyStore(db_path)  # No event_publisher
            store.initialize()

            # Should not raise
            key = store.create_key(principal_id="test", name="Key")
            assert key is not None

            store.close()


class TestSQLiteRoleStoreEvents:
    """Test that SQLiteRoleStore emits domain events."""

    @pytest.fixture
    def event_publisher(self):
        """Create mock event publisher."""
        return Mock()

    @pytest.fixture
    def store(self, event_publisher):
        """Create store with event publisher."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteRoleStore(db_path, event_publisher=event_publisher)
            store.initialize()
            yield store
            store.close()

    def test_assign_role_emits_event(self, store, event_publisher):
        """Assigning a role emits RoleAssigned event."""
        store.assign_role(
            principal_id="user-1",
            role_name="developer",
            scope="global",
            assigned_by="admin",
        )

        event_publisher.assert_called_once()
        event = event_publisher.call_args[0][0]

        assert isinstance(event, RoleAssigned)
        assert event.principal_id == "user-1"
        assert event.role_name == "developer"
        assert event.scope == "global"
        assert event.assigned_by == "admin"

    def test_assign_duplicate_role_no_event(self, store, event_publisher):
        """Assigning same role twice only emits one event."""
        store.assign_role("user-1", "developer")
        event_publisher.reset_mock()

        # Assign again - INSERT OR IGNORE, no rowcount
        store.assign_role("user-1", "developer")

        # Should not emit event for duplicate
        event_publisher.assert_not_called()

    def test_revoke_role_emits_event(self, store, event_publisher):
        """Revoking a role emits RoleRevoked event."""
        store.assign_role("user-1", "developer")
        event_publisher.reset_mock()

        store.revoke_role(
            principal_id="user-1",
            role_name="developer",
            scope="global",
            revoked_by="admin",
        )

        event_publisher.assert_called_once()
        event = event_publisher.call_args[0][0]

        assert isinstance(event, RoleRevoked)
        assert event.principal_id == "user-1"
        assert event.role_name == "developer"
        assert event.revoked_by == "admin"

    def test_revoke_nonexistent_role_no_event(self, store, event_publisher):
        """Revoking nonexistent role does not emit event."""
        store.revoke_role("user-1", "developer")

        event_publisher.assert_not_called()

    def test_scoped_role_emits_event_with_scope(self, store, event_publisher):
        """Scoped role assignment includes scope in event."""
        store.assign_role(
            principal_id="user-1",
            role_name="developer",
            scope="tenant:team-a",
            assigned_by="admin",
        )

        event = event_publisher.call_args[0][0]
        assert event.scope == "tenant:team-a"


class TestEventBusIntegration:
    """Test integration with actual EventBus."""

    def test_with_event_bus(self):
        """Auth stores work with real EventBus."""
        from mcp_hangar.infrastructure.event_bus import EventBus

        event_bus = EventBus()
        received_events = []

        # Subscribe to all events
        event_bus.subscribe_to_all(lambda e: received_events.append(e))

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"

            # Create store with EventBus.publish
            store = SQLiteApiKeyStore(db_path, event_publisher=event_bus.publish)
            store.initialize()

            # Create key
            store.create_key("test-user", "Test Key", created_by="system")

            # Should have received event
            assert len(received_events) == 1
            assert isinstance(received_events[0], ApiKeyCreated)

            store.close()
