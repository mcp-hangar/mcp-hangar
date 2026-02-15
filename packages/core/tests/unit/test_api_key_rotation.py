"""Tests for API key rotation functionality.

Tests the rotate_key() feature on InMemoryApiKeyStore with configurable grace period.
"""

import time

import pytest

from mcp_hangar.domain.contracts.authentication import AuthRequest
from mcp_hangar.domain.events import KeyRotated
from mcp_hangar.domain.exceptions import ExpiredCredentialsError
from mcp_hangar.infrastructure.auth.api_key_authenticator import (
    ApiKeyAuthenticator,
    InMemoryApiKeyStore,
)


class TestApiKeyRotation:
    """Test suite for API key rotation with grace period."""

    def test_rotate_key_returns_new_raw_key(self):
        """rotate_key() should generate and return a new raw API key."""
        store = InMemoryApiKeyStore()
        old_key = store.create_key(
            principal_id="test-principal",
            name="test-key",
        )
        old_key_hash = ApiKeyAuthenticator._hash_key(old_key)

        # Get the key_id before rotation - use internal API for testing
        # In production, key_id would be returned from create_key or stored separately
        with store._lock:
            metadata, _ = store._keys[old_key_hash]
            key_id = metadata.key_id

        # Rotate the key
        new_key = store.rotate_key(key_id=key_id)

        # New key should be different from old key
        assert new_key != old_key
        assert new_key.startswith("mcp_")
        assert len(new_key) > len("mcp_")

    def test_old_key_valid_during_grace_period(self):
        """Old key should remain valid during grace period."""
        store = InMemoryApiKeyStore()
        authenticator = ApiKeyAuthenticator(store)

        old_key = store.create_key(
            principal_id="test-principal",
            name="test-key",
        )
        old_key_hash = ApiKeyAuthenticator._hash_key(old_key)
        metadata = store.get_key_by_hash(old_key_hash)
        key_id = metadata.key_id

        # Rotate with 10 second grace period
        store.rotate_key(key_id=key_id, grace_period_seconds=10)

        # Old key should still authenticate during grace period
        request = AuthRequest(
            headers={"X-API-Key": old_key},
            source_ip="127.0.0.1",
        )
        principal = authenticator.authenticate(request)
        assert principal.id.value == "test-principal"

    def test_old_key_rejected_after_grace_period(self):
        """Old key should be rejected after grace period expires."""
        store = InMemoryApiKeyStore()
        authenticator = ApiKeyAuthenticator(store)

        old_key = store.create_key(
            principal_id="test-principal",
            name="test-key",
        )
        old_key_hash = ApiKeyAuthenticator._hash_key(old_key)
        metadata = store.get_key_by_hash(old_key_hash)
        key_id = metadata.key_id

        # Rotate with 1 second grace period
        store.rotate_key(key_id=key_id, grace_period_seconds=1)

        # Wait for grace period to expire
        time.sleep(1.1)

        # Old key should now be rejected
        request = AuthRequest(
            headers={"X-API-Key": old_key},
            source_ip="127.0.0.1",
        )
        with pytest.raises(ExpiredCredentialsError) as exc_info:
            authenticator.authenticate(request)

        assert "rotated" in str(exc_info.value).lower()

    def test_new_key_valid_immediately(self):
        """New key should be valid immediately after rotation."""
        store = InMemoryApiKeyStore()
        authenticator = ApiKeyAuthenticator(store)

        old_key = store.create_key(
            principal_id="test-principal",
            name="test-key",
        )
        old_key_hash = ApiKeyAuthenticator._hash_key(old_key)
        metadata = store.get_key_by_hash(old_key_hash)
        key_id = metadata.key_id

        # Rotate the key
        new_key = store.rotate_key(key_id=key_id)

        # New key should authenticate immediately
        request = AuthRequest(
            headers={"X-API-Key": new_key},
            source_ip="127.0.0.1",
        )
        principal = authenticator.authenticate(request)
        assert principal.id.value == "test-principal"

    def test_rotate_key_emits_key_rotated_event(self):
        """rotate_key() should emit KeyRotated domain event."""
        events = []

        def event_publisher(event):
            events.append(event)

        store = InMemoryApiKeyStore(event_publisher=event_publisher)

        old_key = store.create_key(
            principal_id="test-principal",
            name="test-key",
        )
        old_key_hash = ApiKeyAuthenticator._hash_key(old_key)
        metadata = store.get_key_by_hash(old_key_hash)
        key_id = metadata.key_id

        # Clear events from creation
        events.clear()

        # Rotate the key
        store.rotate_key(key_id=key_id, grace_period_seconds=3600)

        # Should have emitted KeyRotated event
        rotated_events = [e for e in events if isinstance(e, KeyRotated)]
        assert len(rotated_events) == 1

        event = rotated_events[0]
        assert event.key_id == key_id
        assert event.principal_id == "test-principal"
        assert event.new_key_id != key_id
        assert event.rotated_at > 0
        assert event.grace_until > event.rotated_at

    def test_grace_period_configurable(self):
        """Grace period should be configurable via parameter."""
        store = InMemoryApiKeyStore()

        old_key = store.create_key(
            principal_id="test-principal",
            name="test-key",
        )
        old_key_hash = ApiKeyAuthenticator._hash_key(old_key)
        metadata = store.get_key_by_hash(old_key_hash)
        key_id = metadata.key_id

        grace_period = 7200  # 2 hours
        before = time.time()
        store.rotate_key(key_id=key_id, grace_period_seconds=grace_period)
        after = time.time()

        # Verify grace_until is approximately now + grace_period
        # Access internal state for testing
        with store._lock:
            if old_key_hash in store._rotated_keys:
                _, grace_until = store._rotated_keys[old_key_hash]
                expected_min = before + grace_period
                expected_max = after + grace_period
                assert expected_min <= grace_until <= expected_max
            else:
                pytest.fail("Old key should be in _rotated_keys")

    def test_rotate_nonexistent_key_raises(self):
        """Rotating a non-existent key should raise ValueError."""
        store = InMemoryApiKeyStore()

        with pytest.raises(ValueError) as exc_info:
            store.rotate_key(key_id="nonexistent-key-id")

        assert "not found" in str(exc_info.value).lower() or "unknown" in str(exc_info.value).lower()

    def test_rotate_revoked_key_raises(self):
        """Rotating an already-revoked key should raise ValueError."""
        store = InMemoryApiKeyStore()

        old_key = store.create_key(
            principal_id="test-principal",
            name="test-key",
        )
        old_key_hash = ApiKeyAuthenticator._hash_key(old_key)
        metadata = store.get_key_by_hash(old_key_hash)
        key_id = metadata.key_id

        # Revoke the key
        store.revoke_key(key_id)

        # Try to rotate revoked key
        with pytest.raises(ValueError) as exc_info:
            store.rotate_key(key_id=key_id)

        assert "revoked" in str(exc_info.value).lower()

    def test_rotate_already_rotated_key_raises(self):
        """Rotating a key that's already in grace period should raise ValueError."""
        store = InMemoryApiKeyStore()

        old_key = store.create_key(
            principal_id="test-principal",
            name="test-key",
        )
        old_key_hash = ApiKeyAuthenticator._hash_key(old_key)
        metadata = store.get_key_by_hash(old_key_hash)
        key_id = metadata.key_id

        # Rotate once
        store.rotate_key(key_id=key_id, grace_period_seconds=3600)

        # Try to rotate the same (old) key again
        with pytest.raises(ValueError) as exc_info:
            store.rotate_key(key_id=key_id)

        assert "pending rotation" in str(exc_info.value).lower() or "already rotated" in str(exc_info.value).lower()

    def test_default_grace_period_24h(self):
        """Default grace period should be 86400 seconds (24 hours)."""
        store = InMemoryApiKeyStore()

        old_key = store.create_key(
            principal_id="test-principal",
            name="test-key",
        )
        old_key_hash = ApiKeyAuthenticator._hash_key(old_key)
        metadata = store.get_key_by_hash(old_key_hash)
        key_id = metadata.key_id

        before = time.time()
        store.rotate_key(key_id=key_id)  # No grace_period_seconds specified
        after = time.time()

        # Verify default grace period is 24h (86400s)
        with store._lock:
            if old_key_hash in store._rotated_keys:
                _, grace_until = store._rotated_keys[old_key_hash]
                expected_min = before + 86400
                expected_max = after + 86400
                assert expected_min <= grace_until <= expected_max
            else:
                pytest.fail("Old key should be in _rotated_keys")
