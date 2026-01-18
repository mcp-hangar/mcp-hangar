"""Tests for Auth Projections."""

import time

import pytest

from mcp_hangar.domain.events import ApiKeyCreated, ApiKeyRevoked, RoleAssigned, RoleRevoked
from mcp_hangar.infrastructure.auth.projections import AuthAuditLog, AuthProjection
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore


class TestAuthProjection:
    """Tests for AuthProjection read model."""

    @pytest.fixture
    def projection(self):
        return AuthProjection()

    def test_apply_key_created(self, projection):
        """Applying ApiKeyCreated creates read model."""
        event = ApiKeyCreated(
            key_id="key-1",
            principal_id="user:test",
            key_name="Test Key",
            expires_at=None,
            created_by="admin",
        )

        projection.apply(event)

        model = projection.get_key_by_id("key-1")
        assert model is not None
        assert model.principal_id == "user:test"
        assert model.name == "Test Key"
        assert model.created_by == "admin"
        assert model.revoked is False

    def test_apply_key_revoked(self, projection):
        """Applying ApiKeyRevoked updates read model."""
        # First create
        projection.apply(
            ApiKeyCreated(
                key_id="key-1",
                principal_id="user:test",
                key_name="Test Key",
                expires_at=None,
                created_by="admin",
            )
        )

        # Then revoke
        projection.apply(
            ApiKeyRevoked(
                key_id="key-1",
                principal_id="user:test",
                revoked_by="admin",
                reason="Testing",
            )
        )

        model = projection.get_key_by_id("key-1")
        assert model.revoked is True
        assert model.revoked_by == "admin"
        assert model.revocation_reason == "Testing"

    def test_get_keys_for_principal(self, projection):
        """Can get all keys for a principal."""
        projection.apply(
            ApiKeyCreated(
                key_id="key-1",
                principal_id="user:test",
                key_name="Key 1",
                expires_at=None,
                created_by="admin",
            )
        )
        projection.apply(
            ApiKeyCreated(
                key_id="key-2",
                principal_id="user:test",
                key_name="Key 2",
                expires_at=None,
                created_by="admin",
            )
        )
        projection.apply(
            ApiKeyCreated(
                key_id="key-3",
                principal_id="user:other",
                key_name="Key 3",
                expires_at=None,
                created_by="admin",
            )
        )

        keys = projection.get_keys_for_principal("user:test")

        assert len(keys) == 2
        names = {k.name for k in keys}
        assert names == {"Key 1", "Key 2"}

    def test_get_active_key_count(self, projection):
        """Can count active keys for a principal."""
        projection.apply(
            ApiKeyCreated(
                key_id="key-1",
                principal_id="user:test",
                key_name="Key 1",
                expires_at=None,
                created_by="admin",
            )
        )
        projection.apply(
            ApiKeyCreated(
                key_id="key-2",
                principal_id="user:test",
                key_name="Key 2",
                expires_at=None,
                created_by="admin",
            )
        )
        projection.apply(
            ApiKeyRevoked(
                key_id="key-1",
                principal_id="user:test",
                revoked_by="admin",
                reason="",
            )
        )

        count = projection.get_active_key_count("user:test")

        assert count == 1

    def test_apply_role_assigned(self, projection):
        """Applying RoleAssigned creates read model."""
        event = RoleAssigned(
            principal_id="user:test",
            role_name="developer",
            scope="global",
            assigned_by="admin",
        )

        projection.apply(event)

        roles = projection.get_roles_for_principal("user:test")
        assert len(roles) == 1
        assert roles[0].role_name == "developer"
        assert roles[0].assigned_by == "admin"

    def test_apply_role_revoked(self, projection):
        """Applying RoleRevoked removes from read model."""
        projection.apply(
            RoleAssigned(
                principal_id="user:test",
                role_name="developer",
                scope="global",
                assigned_by="admin",
            )
        )
        projection.apply(
            RoleRevoked(
                principal_id="user:test",
                role_name="developer",
                scope="global",
                revoked_by="admin",
            )
        )

        roles = projection.get_roles_for_principal("user:test")
        assert len(roles) == 0

    def test_has_role(self, projection):
        """has_role correctly checks role assignment."""
        projection.apply(
            RoleAssigned(
                principal_id="user:test",
                role_name="developer",
                scope="tenant:a",
                assigned_by="admin",
            )
        )
        projection.apply(
            RoleAssigned(
                principal_id="user:test",
                role_name="admin",
                scope="global",
                assigned_by="admin",
            )
        )

        # Has developer in tenant:a
        assert projection.has_role("user:test", "developer", "tenant:a") is True

        # Has admin globally
        assert projection.has_role("user:test", "admin", "*") is True
        assert projection.has_role("user:test", "admin", "tenant:a") is True  # global applies

        # Doesn't have developer globally
        assert projection.has_role("user:test", "developer", "global") is False

    def test_get_stats(self, projection):
        """get_stats returns correct statistics."""
        projection.apply(
            ApiKeyCreated(
                key_id="key-1",
                principal_id="user:a",
                key_name="Key 1",
                expires_at=None,
                created_by="admin",
            )
        )
        projection.apply(
            ApiKeyCreated(
                key_id="key-2",
                principal_id="user:b",
                key_name="Key 2",
                expires_at=None,
                created_by="admin",
            )
        )
        projection.apply(
            ApiKeyRevoked(
                key_id="key-1",
                principal_id="user:a",
                revoked_by="admin",
                reason="",
            )
        )
        projection.apply(
            RoleAssigned(
                principal_id="user:a",
                role_name="developer",
                scope="global",
                assigned_by="admin",
            )
        )

        stats = projection.get_stats()

        assert stats["total_api_keys"] == 2
        assert stats["active_api_keys"] == 1
        assert stats["revoked_api_keys"] == 1
        assert stats["total_principals_with_keys"] == 2
        assert stats["total_role_assignments"] == 1

    def test_catchup_from_event_store(self):
        """Can catch up with events from event store."""
        event_store = InMemoryEventStore()

        # Add events to store
        events = [
            ApiKeyCreated(
                key_id="key-1",
                principal_id="user:test",
                key_name="Key 1",
                expires_at=None,
                created_by="admin",
            ),
            RoleAssigned(
                principal_id="user:test",
                role_name="developer",
                scope="global",
                assigned_by="admin",
            ),
        ]
        event_store.append("api_key:hash1", [events[0]], -1)
        event_store.append("role_assignment:user:test", [events[1]], -1)

        # Create projection and catch up
        projection = AuthProjection(event_store)
        count = projection.catchup()

        assert count == 2
        assert projection.get_key_by_id("key-1") is not None
        assert projection.has_role("user:test", "developer") is True


class TestAuthAuditLog:
    """Tests for AuthAuditLog projection."""

    @pytest.fixture
    def audit_log(self):
        return AuthAuditLog(max_entries=100)

    def test_apply_creates_entry(self, audit_log):
        """Applying event creates audit entry."""
        event = ApiKeyCreated(
            key_id="key-1",
            principal_id="user:test",
            key_name="Test Key",
            expires_at=None,
            created_by="admin",
        )

        audit_log.apply(event)

        entries = audit_log.query()
        assert len(entries) == 1
        assert entries[0]["event_type"] == "api_key_created"
        assert entries[0]["principal_id"] == "user:test"

    def test_query_filter_by_principal(self, audit_log):
        """Can filter audit entries by principal."""
        audit_log.apply(
            ApiKeyCreated(
                key_id="key-1",
                principal_id="user:a",
                key_name="Key 1",
                expires_at=None,
                created_by="admin",
            )
        )
        audit_log.apply(
            ApiKeyCreated(
                key_id="key-2",
                principal_id="user:b",
                key_name="Key 2",
                expires_at=None,
                created_by="admin",
            )
        )

        entries = audit_log.query(principal_id="user:a")

        assert len(entries) == 1
        assert entries[0]["details"]["key_id"] == "key-1"

    def test_query_filter_by_event_type(self, audit_log):
        """Can filter audit entries by event type."""
        audit_log.apply(
            ApiKeyCreated(
                key_id="key-1",
                principal_id="user:test",
                key_name="Key 1",
                expires_at=None,
                created_by="admin",
            )
        )
        audit_log.apply(
            RoleAssigned(
                principal_id="user:test",
                role_name="developer",
                scope="global",
                assigned_by="admin",
            )
        )

        entries = audit_log.query(event_type="role_assigned")

        assert len(entries) == 1
        assert entries[0]["event_type"] == "role_assigned"

    def test_query_limit(self, audit_log):
        """Query respects limit."""
        for i in range(10):
            audit_log.apply(
                ApiKeyCreated(
                    key_id=f"key-{i}",
                    principal_id="user:test",
                    key_name=f"Key {i}",
                    expires_at=None,
                    created_by="admin",
                )
            )

        entries = audit_log.query(limit=5)

        assert len(entries) == 5

    def test_max_entries_trimmed(self):
        """Old entries are trimmed when max exceeded."""
        audit_log = AuthAuditLog(max_entries=5)

        for i in range(10):
            audit_log.apply(
                ApiKeyCreated(
                    key_id=f"key-{i}",
                    principal_id="user:test",
                    key_name=f"Key {i}",
                    expires_at=None,
                    created_by="admin",
                )
            )

        entries = audit_log.query(limit=100)

        assert len(entries) == 5

    def test_query_since_timestamp(self, audit_log):
        """Can filter entries by timestamp."""
        # Create first event
        audit_log.apply(
            ApiKeyCreated(
                key_id="key-1",
                principal_id="user:test",
                key_name="Key 1",
                expires_at=None,
                created_by="admin",
            )
        )

        since = time.time()
        time.sleep(0.01)  # Ensure different timestamp

        # Create second event
        audit_log.apply(
            ApiKeyCreated(
                key_id="key-2",
                principal_id="user:test",
                key_name="Key 2",
                expires_at=None,
                created_by="admin",
            )
        )

        entries = audit_log.query(since=since)

        assert len(entries) == 1
        assert entries[0]["details"]["key_id"] == "key-2"
