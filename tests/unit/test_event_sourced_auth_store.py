"""The event-sourced API key and role stores: what replay reconstructs, what it refuses."""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.domain.contracts.authentication import ApiKeyMetadata
from mcp_hangar.domain.contracts.event_store import IEventStore
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
from mcp_hangar.domain.model.event_sourced_api_key import ApiKeySnapshot, EventSourcedApiKey
from mcp_hangar.domain.model.event_sourced_role_assignment import (
    RoleAssignmentSnapshot,
)
from mcp_hangar.domain.value_objects import Permission, PrincipalId, Role


class TestEventSourcedApiKeyStoreGaps:
    """Tests for uncovered paths in EventSourcedApiKeyStore."""

    def _make_store(self, events=None, streams=None, publisher=None, snapshot_store=None):
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
            key_id="kid1",
            principal_id="p1",
            key_name="k1",
            expires_at=None,
            created_by="admin",
        )

        event_store = Mock()
        event_store.list_streams.return_value = ["api_key:hash1"]
        event_store.read_stream.return_value = iter([creation_event])

        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
            key_id="kid1",
            principal_id="p1",
            key_name="test",
            expires_at=None,
            created_by="admin",
        )

        event_store = Mock()
        event_store.list_streams.return_value = ["api_key:h1"]

        # read_stream called multiple times during rotate_key
        event_store.read_stream.return_value = iter([creation_event])
        event_store.append.return_value = 2

        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        store = EventSourcedApiKeyStore(event_store=event_store)
        store._index = {"h1": ("kid1", "p1")}
        store._principal_index = {"p1": {"h1"}}

        # Patch _load_key to return a proper EventSourcedApiKey
        original_key = EventSourcedApiKey.create(
            key_hash="h1",
            key_id="kid1",
            principal_id="p1",
            name="test",
            created_by="admin",
        )
        original_key.collect_events()  # clear events

        with patch.object(store, "_load_key", return_value=original_key):
            raw_key = store.rotate_key("kid1", grace_period_seconds=3600, rotated_by="admin")

        assert raw_key.startswith("mcp_")

    def test_maybe_create_snapshot_below_threshold(self):
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        store = EventSourcedApiKeyStore(event_store=Mock())
        store._maybe_create_snapshot("k1", 10, lambda: "snap")
        assert "k1" not in store._snapshot_store

    def test_maybe_create_snapshot_at_threshold(self):
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        store = EventSourcedApiKeyStore(event_store=Mock())
        create_fn = Mock(return_value="snapshot_data")
        store._maybe_create_snapshot("k1", 50, create_fn)
        assert store._snapshot_store["k1"] == "snapshot_data"
        create_fn.assert_called_once()

    def test_maybe_create_snapshot_existing_snapshot_not_enough_events(self):
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        existing = Mock()
        existing.version = 45
        store = EventSourcedApiKeyStore(event_store=Mock(), snapshot_store={"k1": existing})
        create_fn = Mock()
        store._maybe_create_snapshot("k1", 60, create_fn)
        create_fn.assert_not_called()  # only 15 events since last snapshot

    def test_load_key_with_snapshot(self):
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore
        from mcp_hangar.domain.model.event_sourced_api_key import ApiKeySnapshot

        snapshot = ApiKeySnapshot(
            key_hash="h1",
            key_id="kid1",
            principal_id="p1",
            name="test",
            tenant_id=None,
            groups=[],
            created_at=datetime.now(UTC).timestamp(),
            expires_at=None,
            last_used_at=None,
            revoked=False,
            revoked_at=None,
            rotated_to_key_id=None,
            grace_until=None,
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


class TestEventSourcedRoleStoreGaps:
    """Tests for uncovered paths in EventSourcedRoleStore."""

    def _make_store(self, publisher=None):
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

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
        from mcp_hangar.auth.roles import BUILTIN_ROLES
        from mcp_hangar.domain.exceptions import CannotModifyBuiltinRoleError

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
        from mcp_hangar.domain.value_objects import Role

        store, _ = self._make_store()
        role = Role(name="temp-role", permissions=frozenset(), description="Temporary")
        store.add_role(role)

        store.delete_role("temp-role")
        assert store.get_role("temp-role") is None

    def test_update_role_builtin_raises(self):
        from mcp_hangar.auth.roles import BUILTIN_ROLES
        from mcp_hangar.domain.exceptions import CannotModifyBuiltinRoleError

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
        from mcp_hangar.auth.roles import BUILTIN_ROLES
        from mcp_hangar.domain.value_objects import Role

        store, _ = self._make_store()
        builtin_name = next(iter(BUILTIN_ROLES))

        with pytest.raises(ValueError, match="built-in"):
            store.add_role(Role(name=builtin_name, permissions=frozenset()))

    def test_get_role_builtin(self):
        from mcp_hangar.auth.roles import BUILTIN_ROLES

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


@pytest.fixture
def mock_event_store():
    """Create a mock IEventStore for event-sourced store tests."""
    store = Mock(spec=IEventStore)
    store.list_streams.return_value = []
    store.read_stream.return_value = []
    store.get_stream_version.return_value = -1
    store.append.return_value = 1
    return store


class TestEventSourcedApiKeyStoreLoadKey:
    """Tests for EventSourcedApiKeyStore._load_key() edge cases."""

    def test_load_key_without_index_entry_and_without_snapshot(self, mock_event_store):
        """Lines 150-153, 159-165: _load_key without index_entry, rebuilds from events."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        mock_event_store.read_stream.return_value = []

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        key = store._load_key("unknown-hash")

        assert key is None

    def test_load_key_returns_none_when_no_creation_event(self, mock_event_store):
        """Lines 162-163: no creation event in stream returns None."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        store = EventSourcedApiKeyStore(event_store=mock_event_store)
        # Should not raise
        store._publish_events(
            [
                ApiKeyCreated(
                    key_id="kid",
                    principal_id="svc",
                    key_name="key",
                    expires_at=None,
                    created_by="admin",
                )
            ]
        )


class TestEventSourcedApiKeyStoreSaveKey:
    """Tests for EventSourcedApiKeyStore._save_key()."""

    def test_save_key_updates_index(self, mock_event_store):
        """Lines 205, 217-222: save_key updates the index."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        result = store.get_principal_for_key("missing-hash")
        assert result is None

    def test_returns_principal_for_valid_key(self, mock_event_store):
        """Lines 247-276: successful principal lookup."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        result = store.revoke_key("nonexistent")
        assert result is False

    def test_revoke_already_revoked_returns_false(self, mock_event_store):
        """Lines 345: already revoked returns False."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        keys = store.list_keys("nobody")
        assert keys == []

    def test_count_keys_counts_valid_only(self, mock_event_store):
        """Lines 384-396: count_keys only counts valid keys."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        assert store.count_keys("unknown") == 0


class TestEventSourcedApiKeyStoreRotateKey:
    """Tests for EventSourcedApiKeyStore.rotate_key()."""

    def test_rotate_key_success(self, mock_event_store):
        """Lines 398-470: successful rotation."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        with pytest.raises(ValueError, match="not found"):
            store.rotate_key("nonexistent")

    def test_rotate_key_load_fails_raises(self, mock_event_store):
        """Lines 433-434: key in index but load returns None."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore

        mock_event_store.list_streams.return_value = []
        store = EventSourcedApiKeyStore(event_store=mock_event_store)

        # Manually set up index with a key that can't be loaded
        store._index = {"phantom-hash": ("phantom-kid", "svc-phantom")}
        store._principal_index = {"svc-phantom": {"phantom-hash"}}
        mock_event_store.read_stream.return_value = []

        with pytest.raises(ValueError, match="not found"):
            store.rotate_key("phantom-kid")


class TestEventSourcedRoleStoreGetRole:
    """Tests for EventSourcedRoleStore.get_role()."""

    def test_get_builtin_role(self, mock_event_store):
        """Lines 592-596: get builtin role."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        role = store.get_role("admin")

        assert role is not None
        assert role.name == "admin"

    def test_get_custom_role(self, mock_event_store):
        """Lines 598-599: get custom role."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        custom = Role(name="custom", description="test", permissions=frozenset())
        store.add_role(custom)

        result = store.get_role("custom")
        assert result is not None
        assert result.name == "custom"

    def test_get_nonexistent_role(self, mock_event_store):
        """get_role returns None for unknown role."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        assert store.get_role("phantom") is None


class TestEventSourcedRoleStoreAddRole:
    """Tests for EventSourcedRoleStore.add_role()."""

    def test_add_custom_role(self, mock_event_store):
        """Lines 601-607: add custom role."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        role = Role(name="tester", description="test role", permissions=frozenset())
        store.add_role(role)

        assert store.get_role("tester") is not None

    def test_add_builtin_role_raises(self, mock_event_store):
        """Lines 603-604: cannot override builtin role."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        role = Role(name="admin", description="override", permissions=frozenset())

        with pytest.raises(ValueError, match="built-in"):
            store.add_role(role)


class TestEventSourcedRoleStoreAssignRole:
    """Tests for EventSourcedRoleStore.assign_role()."""

    def test_assign_role_to_principal(self, mock_event_store):
        """Lines 626-648: assign role."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        store.assign_role("svc-1", "admin", scope="global", assigned_by="system")

        mock_event_store.append.assert_called_once()

    def test_assign_unknown_role_raises(self, mock_event_store):
        """Lines 635-636: assign unknown role raises."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)

        with pytest.raises(ValueError, match="Unknown role"):
            store.assign_role("svc-1", "nonexistent")

    def test_assign_already_assigned_is_noop(self, mock_event_store):
        """Duplicate assignment does not save events."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        mock_event_store.read_stream.return_value = []
        store = EventSourcedRoleStore(event_store=mock_event_store)

        store.revoke_role("svc-no", "admin", scope="global")
        mock_event_store.append.assert_not_called()

    def test_revoke_role_publishes_events(self, mock_event_store):
        """revoke_role publishes events when publisher is set."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        mock_event_store.read_stream.return_value = []
        store = EventSourcedRoleStore(event_store=mock_event_store)

        roles = store.get_roles_for_principal("nobody")
        assert roles == []


class TestEventSourcedRoleStoreListAllRoles:
    """Tests for EventSourcedRoleStore.list_all_roles()."""

    def test_list_all_custom_roles(self, mock_event_store):
        """Lines 670-673: list custom roles."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        store.add_role(Role(name="to-del", description="del", permissions=frozenset()))
        store.delete_role("to-del")

        assert store.get_role("to-del") is None

    def test_delete_builtin_raises(self, mock_event_store):
        """Lines 687-688: cannot delete builtin."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)

        with pytest.raises(CannotModifyBuiltinRoleError):
            store.delete_role("admin")

    def test_delete_nonexistent_raises(self, mock_event_store):
        """Lines 690-691: role not found."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)

        with pytest.raises(RoleNotFoundError):
            store.delete_role("phantom")


class TestEventSourcedRoleStoreUpdateRole:
    """Tests for EventSourcedRoleStore.update_role()."""

    def test_update_custom_role(self, mock_event_store):
        """Lines 695-728: update custom role."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        store.add_role(Role(name="updatable", description="old", permissions=frozenset()))

        new_perms = [Permission(resource_type="tool", action="write", resource_id="*")]
        updated = store.update_role("updatable", permissions=new_perms, description="new")

        assert updated.description == "new"
        assert len(updated.permissions) == 1

    def test_update_builtin_raises(self, mock_event_store):
        """Lines 717-718: cannot update builtin."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)

        with pytest.raises(CannotModifyBuiltinRoleError):
            store.update_role("admin", permissions=[], description="hack")

    def test_update_nonexistent_raises(self, mock_event_store):
        """Lines 720-721: role not found."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)

        with pytest.raises(RoleNotFoundError):
            store.update_role("phantom", permissions=[], description="")


class TestEventSourcedRoleStoreLoadAssignment:
    """Tests for EventSourcedRoleStore._load_assignment()."""

    def test_load_from_snapshot(self, mock_event_store):
        """Lines 525-526: load from snapshot."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        mock_event_store.read_stream.return_value = []
        store = EventSourcedRoleStore(event_store=mock_event_store)

        assignment = store._load_assignment("brand-new")
        assert assignment.principal_id == "brand-new"
        assert assignment.get_role_names() == set()


class TestEventSourcedRoleStorePublishEvents:
    """Tests for EventSourcedRoleStore._publish_events()."""

    def test_publish_with_publisher(self, mock_event_store):
        """Lines 533-536: publish events when publisher is set."""
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

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
        from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore

        store = EventSourcedRoleStore(event_store=mock_event_store)
        store._publish_events(
            [
                RoleAssigned(
                    principal_id="svc",
                    role_name="admin",
                    scope="global",
                    assigned_by="system",
                )
            ]
        )
