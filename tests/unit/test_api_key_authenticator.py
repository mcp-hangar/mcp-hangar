"""`ApiKeyAuthenticator` and the in-memory key store behind it."""

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.auth.infrastructure.api_key_authenticator import (
    MAX_API_KEY_LENGTH,
    ApiKeyAuthenticator,
    InMemoryApiKeyStore,
)
from mcp_hangar.domain.contracts.authentication import AuthRequest, IApiKeyStore
from mcp_hangar.domain.exceptions import (
    ExpiredCredentialsError,
    InvalidCredentialsError,
    RevokedCredentialsError,
)
from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType


class TestApiKeyAuthenticator:
    """Tests for ApiKeyAuthenticator class."""

    def _make_authenticator(self, key_store: IApiKeyStore | None = None, header_name: str | None = None):
        store = key_store or Mock(spec=IApiKeyStore)
        return ApiKeyAuthenticator(key_store=store, header_name=header_name), store

    def test_supports_returns_true_when_header_present(self):
        auth, _ = self._make_authenticator()
        request = AuthRequest(headers={"X-API-Key": "mcp_test"}, source_ip="127.0.0.1")
        assert auth.supports(request) is True

    def test_supports_returns_true_for_lowercase_header(self):
        """Line 54: case-insensitive header lookup."""
        auth, _ = self._make_authenticator()
        request = AuthRequest(headers={"x-api-key": "mcp_test"}, source_ip="127.0.0.1")
        assert auth.supports(request) is True

    def test_supports_returns_false_when_header_absent(self):
        auth, _ = self._make_authenticator()
        request = AuthRequest(headers={"Authorization": "Bearer xxx"}, source_ip="127.0.0.1")
        assert auth.supports(request) is False

    def test_supports_with_custom_header_name(self):
        auth, _ = self._make_authenticator(header_name="X-Custom-Key")
        request = AuthRequest(headers={"X-Custom-Key": "mcp_test"}, source_ip="127.0.0.1")
        assert auth.supports(request) is True

    def test_authenticate_empty_header_raises_invalid_credentials(self):
        """Lines 71-77: empty key raises InvalidCredentialsError."""
        auth, _ = self._make_authenticator()
        request = AuthRequest(headers={"X-API-Key": ""}, source_ip="1.2.3.4")
        with pytest.raises(InvalidCredentialsError, match="API key header is empty"):
            auth.authenticate(request)

    def test_authenticate_missing_header_raises_invalid_credentials(self):
        """Line 71: key not found in either case returns empty string."""
        auth, _ = self._make_authenticator()
        request = AuthRequest(headers={}, source_ip="1.2.3.4")
        with pytest.raises(InvalidCredentialsError, match="API key header is empty"):
            auth.authenticate(request)

    def test_authenticate_key_too_long_raises_invalid_credentials(self):
        """Lines 80-84: key exceeds MAX_API_KEY_LENGTH."""
        auth, _ = self._make_authenticator()
        long_key = "mcp_" + "a" * (MAX_API_KEY_LENGTH + 1)
        request = AuthRequest(headers={"X-API-Key": long_key}, source_ip="1.2.3.4")
        with pytest.raises(InvalidCredentialsError, match="exceeds maximum length"):
            auth.authenticate(request)

    def test_authenticate_missing_prefix_raises_invalid_credentials(self):
        """Lines 86-90: key without mcp_ prefix."""
        auth, _ = self._make_authenticator()
        request = AuthRequest(headers={"X-API-Key": "invalid_no_prefix"}, source_ip="1.2.3.4")
        with pytest.raises(InvalidCredentialsError, match="must start with"):
            auth.authenticate(request)

    def test_authenticate_unknown_key_raises_invalid_credentials(self):
        """Lines 97-107: key_store returns None."""
        auth, store = self._make_authenticator()
        store.get_principal_for_key.return_value = None
        request = AuthRequest(headers={"X-API-Key": "mcp_validkey123"}, source_ip="1.2.3.4")
        with pytest.raises(InvalidCredentialsError, match="Invalid API key"):
            auth.authenticate(request)

    def test_authenticate_success_returns_principal(self):
        """Lines 109-116: successful authentication."""
        principal = Principal(
            id=PrincipalId("svc-test"),
            type=PrincipalType.SERVICE_ACCOUNT,
        )
        auth, store = self._make_authenticator()
        store.get_principal_for_key.return_value = principal
        request = AuthRequest(headers={"X-API-Key": "mcp_validkey123"}, source_ip="1.2.3.4")
        result = auth.authenticate(request)
        assert result == principal

    def test_authenticate_uses_lowercase_header_fallback(self):
        """Line 71: case-insensitive header lookup via lowercase fallback."""
        principal = Principal(id=PrincipalId("svc-test"), type=PrincipalType.SERVICE_ACCOUNT)
        auth, store = self._make_authenticator()
        store.get_principal_for_key.return_value = principal
        request = AuthRequest(headers={"x-api-key": "mcp_validkey123"}, source_ip="1.2.3.4")
        result = auth.authenticate(request)
        assert result == principal

    def test_hash_key_returns_sha256(self):
        """Line 128: _hash_key returns SHA-256 hex digest."""
        key = "mcp_test123"
        expected = hashlib.sha256(key.encode()).hexdigest()
        assert ApiKeyAuthenticator._hash_key(key) == expected

    def test_generate_key_has_correct_prefix(self):
        """Lines 137-138: generate_key starts with mcp_ prefix."""
        key = ApiKeyAuthenticator.generate_key()
        assert key.startswith("mcp_")
        assert len(key) > len("mcp_")

    def test_generate_key_is_unique(self):
        """Each call should produce a different key (cryptographic randomness)."""
        keys = {ApiKeyAuthenticator.generate_key() for _ in range(10)}
        assert len(keys) == 10


class TestInMemoryApiKeyStore:
    """Tests for InMemoryApiKeyStore class."""

    def _make_store(self, event_publisher=None):
        return InMemoryApiKeyStore(event_publisher=event_publisher)

    def test_create_key_returns_raw_key_with_prefix(self):
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        assert raw_key.startswith("mcp_")

    def test_create_key_stores_key_and_get_principal_retrieves(self):
        """Lines 194-241: get_principal_for_key finds created key."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        principal = store.get_principal_for_key(key_hash)
        assert principal is not None
        assert principal.id.value == "svc-1"
        assert principal.type == PrincipalType.SERVICE_ACCOUNT

    def test_get_principal_for_key_returns_none_for_unknown(self):
        """Lines 196-198: constant_time_key_lookup returns None."""
        store = self._make_store()
        result = store.get_principal_for_key("nonexistent_hash")
        assert result is None

    def test_get_principal_for_key_raises_on_revoked(self):
        """Lines 202-206: revoked key raises RevokedCredentialsError."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        # Get metadata to find key_id
        metadata = list(store._keys.values())[0][0]
        store.revoke_key(metadata.key_id)
        with pytest.raises(RevokedCredentialsError, match="revoked"):
            store.get_principal_for_key(key_hash)

    def test_get_principal_for_key_raises_on_expired(self):
        """Lines 208-213: expired key raises ExpiredCredentialsError."""
        store = self._make_store()
        expired_time = datetime.now(UTC) - timedelta(hours=1)
        raw_key = store.create_key(principal_id="svc-1", name="test-key", expires_at=expired_time)
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        with pytest.raises(ExpiredCredentialsError, match="expired"):
            store.get_principal_for_key(key_hash)

    def test_get_principal_for_key_updates_last_used_at(self):
        """Lines 230-239: last_used_at is updated on access."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        # First access
        store.get_principal_for_key(key_hash)
        metadata_after = store._keys[key_hash][0]
        assert metadata_after.last_used_at is not None

    def test_get_principal_for_key_rotated_within_grace_period(self):
        """Lines 216-225: rotated key within grace period is allowed."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        # Rotate with large grace period
        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 1000.0
            _ = store.rotate_key(metadata.key_id, grace_period_seconds=3600)
        # Access old key within grace period
        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 1500.0  # Within grace period
            principal = store.get_principal_for_key(key_hash)
        assert principal is not None
        assert principal.id.value == "svc-1"

    def test_get_principal_for_key_rotated_grace_expired_raises(self):
        """Lines 219-224: rotated key after grace period raises ExpiredCredentialsError."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        # Rotate with short grace period
        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 1000.0
            store.rotate_key(metadata.key_id, grace_period_seconds=10)
        # Access old key after grace period
        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 2000.0  # Well past grace period
            with pytest.raises(ExpiredCredentialsError, match="rotated"):
                store.get_principal_for_key(key_hash)

    def test_create_key_with_groups_and_tenant(self):
        store = self._make_store()
        raw_key = store.create_key(
            principal_id="svc-1",
            name="test-key",
            groups=frozenset({"admin", "ops"}),
            tenant_id="tenant-abc",
        )
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        principal = store.get_principal_for_key(key_hash)
        assert principal.groups == frozenset({"admin", "ops"})
        assert principal.tenant_id == "tenant-abc"

    def test_create_key_max_keys_limit_raises(self):
        """Line 272: exceeding MAX_KEYS_PER_PRINCIPAL raises ValueError."""
        store = self._make_store()
        store.MAX_KEYS_PER_PRINCIPAL = 2  # Lower limit for testing
        store.create_key(principal_id="svc-1", name="key1")
        store.create_key(principal_id="svc-1", name="key2")
        with pytest.raises(ValueError, match="maximum number of API keys"):
            store.create_key(principal_id="svc-1", name="key3")

    def test_revoke_key_success(self):
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        result = store.revoke_key(metadata.key_id)
        assert result is True

    def test_revoke_key_not_found(self):
        """Lines 350-351: revoke nonexistent key returns False."""
        store = self._make_store()
        result = store.revoke_key("nonexistent-id")
        assert result is False

    def test_list_keys_returns_metadata_for_principal(self):
        store = self._make_store()
        store.create_key(principal_id="svc-1", name="key1")
        store.create_key(principal_id="svc-1", name="key2")
        store.create_key(principal_id="svc-2", name="key3")
        keys = store.list_keys("svc-1")
        assert len(keys) == 2
        names = {k.name for k in keys}
        assert names == {"key1", "key2"}

    def test_get_key_by_id_found(self):
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        result = store.get_key_by_id(metadata.key_id)
        assert result is not None
        assert result.key_id == metadata.key_id

    def test_get_key_by_id_not_found(self):
        store = self._make_store()
        result = store.get_key_by_id("nonexistent")
        assert result is None

    def test_count_keys_active_only(self):
        """Lines 393-399: count_keys counts active non-expired keys."""
        store = self._make_store()
        store.create_key(principal_id="svc-1", name="active-key")
        expired_time = datetime.now(UTC) - timedelta(hours=1)
        store.create_key(principal_id="svc-1", name="expired-key", expires_at=expired_time)
        # Revoke one
        raw_key3 = store.create_key(principal_id="svc-1", name="revoked-key")
        key_hash3 = ApiKeyAuthenticator._hash_key(raw_key3)
        meta3 = store._keys[key_hash3][0]
        store.revoke_key(meta3.key_id)

        count = store.count_keys("svc-1")
        assert count == 1  # Only the active, non-expired one

    def test_count_all_keys(self):
        """Lines 407-408: count_all_keys includes revoked keys."""
        store = self._make_store()
        store.create_key(principal_id="svc-1", name="key1")
        raw_key2 = store.create_key(principal_id="svc-1", name="key2")
        key_hash2 = ApiKeyAuthenticator._hash_key(raw_key2)
        meta2 = store._keys[key_hash2][0]
        store.revoke_key(meta2.key_id)
        assert store.count_all_keys() == 2

    def test_count_all_active_keys(self):
        """Lines 416-423: count_all_active_keys excludes revoked and expired."""
        store = self._make_store()
        store.create_key(principal_id="svc-1", name="active")
        store.create_key(principal_id="svc-2", name="active2")
        expired_time = datetime.now(UTC) - timedelta(hours=1)
        store.create_key(principal_id="svc-3", name="expired", expires_at=expired_time)
        assert store.count_all_active_keys() == 2

    def test_rotate_key_success(self):
        """Lines 444-522: rotate_key produces new key, marks old as rotated."""
        publisher = Mock()
        store = self._make_store(event_publisher=publisher)
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]

        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 5000.0
            new_raw = store.rotate_key(metadata.key_id, grace_period_seconds=3600, rotated_by="admin")

        assert new_raw.startswith("mcp_")
        assert new_raw != raw_key
        # Old key should be in rotated_keys
        assert key_hash in store._rotated_keys
        _, grace_until = store._rotated_keys[key_hash]
        assert grace_until == 5000.0 + 3600
        # Event was published
        publisher.assert_called_once()

    def test_rotate_key_not_found_raises(self):
        """Line 458: rotate_key with unknown key_id raises ValueError."""
        store = self._make_store()
        with pytest.raises(ValueError, match="API key not found"):
            store.rotate_key("nonexistent-id")

    def test_rotate_revoked_key_raises(self):
        """Line 462: rotate revoked key raises ValueError."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        store.revoke_key(metadata.key_id)
        with pytest.raises(ValueError, match="Cannot rotate revoked key"):
            store.rotate_key(metadata.key_id)

    def test_rotate_key_already_pending_raises(self):
        """Line 466: rotate key with pending rotation raises ValueError."""
        store = self._make_store()
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 1000.0
            store.rotate_key(metadata.key_id)
        with pytest.raises(ValueError, match="pending rotation"):
            store.rotate_key(metadata.key_id)

    def test_publish_event_swallows_exceptions(self):
        """Lines 173-179: _publish_event logs but does not raise."""
        publisher = Mock(side_effect=RuntimeError("boom"))
        store = self._make_store(event_publisher=publisher)
        raw_key = store.create_key(principal_id="svc-1", name="test-key")
        key_hash = ApiKeyAuthenticator._hash_key(raw_key)
        metadata = store._keys[key_hash][0]
        # Rotate should succeed despite event publish failure
        with patch("mcp_hangar.auth.infrastructure.api_key_authenticator.time") as mock_time:
            mock_time.time.return_value = 1000.0
            new_raw = store.rotate_key(metadata.key_id)
        assert new_raw.startswith("mcp_")

    def test_publish_event_noop_when_no_publisher(self):
        """Lines 173-174: _publish_event does nothing when publisher is None."""
        store = self._make_store(event_publisher=None)
        # No error should occur
        store._publish_event(object())
