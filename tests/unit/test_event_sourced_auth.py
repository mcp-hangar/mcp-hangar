"""Tests for Event Sourced Auth stores."""

import pytest

from mcp_hangar.domain.events import ApiKeyCreated, ApiKeyRevoked, RoleAssigned, RoleRevoked
from mcp_hangar.domain.model.event_sourced_api_key import EventSourcedApiKey
from mcp_hangar.domain.model.event_sourced_role_assignment import EventSourcedRoleAssignment
from mcp_hangar.infrastructure.auth.event_sourced_store import EventSourcedApiKeyStore, EventSourcedRoleStore
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore


class TestEventSourcedApiKey:
    """Tests for EventSourcedApiKey aggregate."""

    def test_create_records_event(self):
        """Creating an API key records ApiKeyCreated event."""
        key = EventSourcedApiKey.create(
            key_hash="abc123",
            key_id="key-1",
            principal_id="user:test",
            name="Test Key",
            created_by="admin",
        )

        events = key.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], ApiKeyCreated)
        assert events[0].key_id == "key-1"
        assert events[0].principal_id == "user:test"
        assert events[0].created_by == "admin"

    def test_revoke_records_event(self):
        """Revoking an API key records ApiKeyRevoked event."""
        key = EventSourcedApiKey.create(
            key_hash="abc123",
            key_id="key-1",
            principal_id="user:test",
            name="Test Key",
            created_by="admin",
        )
        key.collect_events()  # Clear creation event

        key.revoke(revoked_by="admin", reason="testing")

        events = key.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], ApiKeyRevoked)
        assert events[0].key_id == "key-1"
        assert events[0].revoked_by == "admin"
        assert events[0].reason == "testing"

    def test_revoke_twice_raises(self):
        """Revoking an already revoked key raises."""
        key = EventSourcedApiKey.create(
            key_hash="abc123",
            key_id="key-1",
            principal_id="user:test",
            name="Test Key",
            created_by="admin",
        )
        key.revoke(revoked_by="admin")

        with pytest.raises(ValueError, match="already revoked"):
            key.revoke(revoked_by="admin")

    def test_is_valid_checks_revoked_and_expired(self):
        """is_valid checks both revoked and expired status."""
        # Valid key
        key = EventSourcedApiKey.create(
            key_hash="abc123",
            key_id="key-1",
            principal_id="user:test",
            name="Test Key",
            created_by="admin",
        )
        assert key.is_valid is True

        # Revoked key
        key.revoke(revoked_by="admin")
        assert key.is_valid is False

    def test_from_events_rebuilds_state(self):
        """from_events correctly rebuilds state."""
        # Create and revoke
        original = EventSourcedApiKey.create(
            key_hash="abc123",
            key_id="key-1",
            principal_id="user:test",
            name="Test Key",
            created_by="admin",
        )
        original.revoke(revoked_by="admin")

        events = original.collect_events()

        # Rebuild from events
        rebuilt = EventSourcedApiKey.from_events(
            key_hash="abc123",
            key_id="key-1",
            principal_id="user:test",
            name="Test Key",
            events=events,
        )

        assert rebuilt.is_revoked is True
        assert rebuilt._version == 2  # 2 events applied

    def test_snapshot_and_restore(self):
        """Can create snapshot and restore from it."""
        key = EventSourcedApiKey.create(
            key_hash="abc123",
            key_id="key-1",
            principal_id="user:test",
            name="Test Key",
            created_by="admin",
            tenant_id="tenant-1",
            groups=frozenset(["group-a"]),
        )
        key.collect_events()

        snapshot = key.create_snapshot()

        restored = EventSourcedApiKey.from_snapshot(snapshot)

        assert restored.key_hash == key.key_hash
        assert restored.key_id == key.key_id
        assert restored.principal_id == key.principal_id
        assert restored._tenant_id == "tenant-1"


class TestEventSourcedRoleAssignment:
    """Tests for EventSourcedRoleAssignment aggregate."""

    def test_assign_role_records_event(self):
        """Assigning a role records RoleAssigned event."""
        assignment = EventSourcedRoleAssignment("user:test")

        result = assignment.assign_role("developer", "global", "admin")

        assert result is True
        events = assignment.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], RoleAssigned)
        assert events[0].principal_id == "user:test"
        assert events[0].role_name == "developer"
        assert events[0].assigned_by == "admin"

    def test_assign_duplicate_returns_false(self):
        """Assigning same role twice returns False."""
        assignment = EventSourcedRoleAssignment("user:test")
        assignment.assign_role("developer", "global", "admin")
        assignment.collect_events()

        result = assignment.assign_role("developer", "global", "admin")

        assert result is False
        assert len(assignment.collect_events()) == 0

    def test_revoke_role_records_event(self):
        """Revoking a role records RoleRevoked event."""
        assignment = EventSourcedRoleAssignment("user:test")
        assignment.assign_role("developer")
        assignment.collect_events()

        result = assignment.revoke_role("developer", "global", "admin")

        assert result is True
        events = assignment.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], RoleRevoked)

    def test_revoke_unassigned_returns_false(self):
        """Revoking unassigned role returns False."""
        assignment = EventSourcedRoleAssignment("user:test")

        result = assignment.revoke_role("developer")

        assert result is False

    def test_get_role_names_all_scopes(self):
        """get_role_names with '*' returns all roles."""
        assignment = EventSourcedRoleAssignment("user:test")
        assignment.assign_role("admin", "global")
        assignment.assign_role("developer", "tenant:a")
        assignment.assign_role("viewer", "tenant:b")

        roles = assignment.get_role_names("*")

        assert roles == {"admin", "developer", "viewer"}

    def test_get_role_names_specific_scope(self):
        """get_role_names with specific scope includes global."""
        assignment = EventSourcedRoleAssignment("user:test")
        assignment.assign_role("admin", "global")
        assignment.assign_role("developer", "tenant:a")
        assignment.assign_role("viewer", "tenant:b")

        roles = assignment.get_role_names("tenant:a")

        # Should include tenant:a roles + global roles
        assert roles == {"admin", "developer"}

    def test_from_events_rebuilds_state(self):
        """from_events correctly rebuilds state."""
        original = EventSourcedRoleAssignment("user:test")
        original.assign_role("admin")
        original.assign_role("developer")
        original.revoke_role("developer")

        events = original.collect_events()

        rebuilt = EventSourcedRoleAssignment.from_events("user:test", events)

        assert rebuilt.has_role("admin") is True
        assert rebuilt.has_role("developer") is False


class TestEventSourcedApiKeyStore:
    """Tests for EventSourcedApiKeyStore."""

    @pytest.fixture
    def event_store(self):
        return InMemoryEventStore()

    @pytest.fixture
    def store(self, event_store):
        return EventSourcedApiKeyStore(event_store)

    def test_create_key_persists_event(self, store, event_store):
        """Creating a key persists event to event store."""
        raw_key = store.create_key(
            principal_id="user:test",
            name="Test Key",
            created_by="admin",
        )

        assert raw_key.startswith("mcp_")

        # Check event was persisted
        streams = event_store.list_streams("api_key:")
        assert len(streams) == 1

    def test_create_and_authenticate(self, store):
        """Can create key and then authenticate with it."""
        raw_key = store.create_key(
            principal_id="user:test",
            name="Test Key",
        )

        import hashlib

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        principal = store.get_principal_for_key(key_hash)

        assert principal is not None
        assert principal.id.value == "user:test"

    def test_revoke_key(self, store):
        """Can revoke a key."""
        raw_key = store.create_key(
            principal_id="user:test",
            name="Test Key",
        )

        keys = store.list_keys("user:test")
        key_id = keys[0].key_id

        result = store.revoke_key(key_id, revoked_by="admin")

        assert result is True

        # Verify revoked
        import hashlib

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        from mcp_hangar.domain.exceptions import RevokedCredentialsError

        with pytest.raises(RevokedCredentialsError):
            store.get_principal_for_key(key_hash)

    def test_list_keys(self, store):
        """Can list keys for a principal."""
        store.create_key("user:test", "Key 1")
        store.create_key("user:test", "Key 2")
        store.create_key("user:other", "Other Key")

        keys = store.list_keys("user:test")

        assert len(keys) == 2
        names = {k.name for k in keys}
        assert names == {"Key 1", "Key 2"}

    def test_count_keys(self, store):
        """Can count active keys."""
        store.create_key("user:test", "Key 1")
        store.create_key("user:test", "Key 2")

        keys = store.list_keys("user:test")
        store.revoke_key(keys[0].key_id)

        count = store.count_keys("user:test")

        assert count == 1


class TestEventSourcedRoleStore:
    """Tests for EventSourcedRoleStore."""

    @pytest.fixture
    def event_store(self):
        return InMemoryEventStore()

    @pytest.fixture
    def store(self, event_store):
        return EventSourcedRoleStore(event_store)

    def test_assign_role_persists_event(self, store, event_store):
        """Assigning a role persists event to event store."""
        store.assign_role("user:test", "developer", assigned_by="admin")

        streams = event_store.list_streams("role_assignment:")
        assert len(streams) == 1

    def test_assign_and_get_roles(self, store):
        """Can assign roles and get them back."""
        store.assign_role("user:test", "developer")
        store.assign_role("user:test", "viewer")

        roles = store.get_roles_for_principal("user:test")

        role_names = {r.name for r in roles}
        assert role_names == {"developer", "viewer"}

    def test_revoke_role(self, store):
        """Can revoke a role."""
        store.assign_role("user:test", "developer")
        store.revoke_role("user:test", "developer")

        roles = store.get_roles_for_principal("user:test")

        assert len(roles) == 0

    def test_get_builtin_role(self, store):
        """Can get built-in roles."""
        role = store.get_role("admin")

        assert role is not None
        assert role.name == "admin"

    def test_add_custom_role(self, store):
        """Can add custom roles."""
        from mcp_hangar.domain.value_objects import Permission, Role

        custom_role = Role(
            name="custom-dev",
            description="Custom developer role",
            permissions=frozenset([Permission("tool", "invoke")]),
        )

        store.add_role(custom_role)

        role = store.get_role("custom-dev")
        assert role is not None
        assert role.description == "Custom developer role"


class TestEventSourcedIntegration:
    """Integration tests for Event Sourced auth."""

    def test_full_workflow(self):
        """Test full auth workflow with event sourcing."""
        from mcp_hangar.infrastructure.event_bus import EventBus

        event_store = InMemoryEventStore()
        event_bus = EventBus()

        received_events = []
        event_bus.subscribe_to_all(lambda e: received_events.append(e))

        # Create stores with event_publisher (EventBus implements IEventPublisher)
        api_key_store = EventSourcedApiKeyStore(event_store, event_publisher=event_bus)
        role_store = EventSourcedRoleStore(event_store, event_publisher=event_bus)

        # Create key
        raw_key = api_key_store.create_key(
            principal_id="service:my-app",
            name="Production Key",
            created_by="admin",
        )

        # Assign roles
        role_store.assign_role("service:my-app", "developer")
        role_store.assign_role("service:my-app", "viewer")

        # Verify events were published
        assert len(received_events) == 3  # 1 key + 2 roles
        assert isinstance(received_events[0], ApiKeyCreated)
        assert isinstance(received_events[1], RoleAssigned)
        assert isinstance(received_events[2], RoleAssigned)

        # Verify state
        import hashlib

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        principal = api_key_store.get_principal_for_key(key_hash)
        assert principal.id.value == "service:my-app"

        roles = role_store.get_roles_for_principal("service:my-app")
        assert len(roles) == 2

    def test_rebuild_from_events(self):
        """Test rebuilding state from events (simulating restart)."""
        event_store = InMemoryEventStore()

        # Create initial state
        store1 = EventSourcedApiKeyStore(event_store)
        _ = store1.create_key("user:test", "Key 1")  # Raw key returned but not needed here

        keys = store1.list_keys("user:test")
        key_id = keys[0].key_id
        store1.revoke_key(key_id)

        # Simulate restart - create new store with same event store
        store2 = EventSourcedApiKeyStore(event_store)

        # Should rebuild state from events
        keys = store2.list_keys("user:test")
        assert len(keys) == 1
        assert keys[0].revoked is True
