"""Tests for constant-time authentication to prevent timing attacks.

These tests verify that API key lookup uses constant-time comparison
to prevent timing side-channel attacks that could leak information
about which key hashes exist in the store.
"""

from unittest.mock import patch


from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
from enterprise.auth.infrastructure.constant_time import constant_time_key_lookup


class TestConstantTimeKeyLookup:
    """Tests for the constant_time_key_lookup utility function."""

    def test_finds_existing_key_hash(self):
        """Verify that constant_time_key_lookup returns value for existing hash."""
        hash_dict = {
            "hash1": "value1",
            "hash2": "value2",
            "hash3": "value3",
        }

        result = constant_time_key_lookup("hash2", hash_dict)

        assert result == "value2"

    def test_returns_none_for_missing_key_hash(self):
        """Verify that constant_time_key_lookup returns None for missing hash."""
        hash_dict = {
            "hash1": "value1",
            "hash2": "value2",
        }

        result = constant_time_key_lookup("nonexistent", hash_dict)

        assert result is None

    def test_uses_hmac_compare_digest(self):
        """Verify that hmac.compare_digest is called for EVERY key in dict."""
        hash_dict = {
            "hash1": "value1",
            "hash2": "value2",
            "hash3": "value3",
        }

        with patch("mcp_hangar.infrastructure.auth.constant_time.hmac.compare_digest") as mock_compare:
            # Make compare_digest return True only for hash2
            def side_effect(a, b):
                return b.decode("utf-8") == "hash2"

            mock_compare.side_effect = side_effect

            result = constant_time_key_lookup("hash2", hash_dict)

            # Should be called exactly 3 times (once per key in dict)
            assert mock_compare.call_count == 3
            assert result == "value2"

    def test_iterates_all_keys_regardless_of_match_position(self):
        """Verify no early exit - all keys checked even if match is first."""
        # Create dict with 100 entries, target at position 0
        hash_dict = {f"hash{i}": f"value{i}" for i in range(100)}

        with patch("mcp_hangar.infrastructure.auth.constant_time.hmac.compare_digest") as mock_compare:
            # Make compare_digest return True only for hash0
            def side_effect(a, b):
                return b.decode("utf-8") == "hash0"

            mock_compare.side_effect = side_effect

            result = constant_time_key_lookup("hash0", hash_dict)

            # Should be called exactly 100 times - no early exit
            assert mock_compare.call_count == 100
            assert result == "value0"

    def test_handles_empty_dict(self):
        """Verify that empty dict returns None without error."""
        result = constant_time_key_lookup("any_hash", {})

        assert result is None

    def test_handles_unicode_hashes(self):
        """Verify that unicode strings in hashes work correctly."""
        hash_dict = {
            "hash_with_émojis_🔑": "value1",
            "hash_with_中文": "value2",
            "hash_with_עברית": "value3",
        }

        result = constant_time_key_lookup("hash_with_中文", hash_dict)

        assert result == "value2"


class TestInMemoryApiKeyStoreConstantTime:
    """Tests verifying InMemoryApiKeyStore uses constant-time lookup."""

    def test_get_principal_uses_constant_time_lookup(self):
        """Verify that get_principal_for_key uses constant-time lookup."""
        store = InMemoryApiKeyStore()

        # Create a key
        principal_id = "test-principal"
        raw_key = store.create_key(
            principal_id=principal_id,
            name="test-key",
        )

        # Hash the key (same way authenticator does)
        from enterprise.auth.infrastructure.api_key_authenticator import ApiKeyAuthenticator

        key_hash = ApiKeyAuthenticator._hash_key(raw_key)

        # Verify principal is returned
        principal = store.get_principal_for_key(key_hash)
        assert principal is not None
        assert principal.id.value == principal_id

        # Verify hmac.compare_digest was used during lookup
        with patch("mcp_hangar.infrastructure.auth.constant_time.hmac.compare_digest") as mock_compare:
            # Make it always return True for simplicity
            mock_compare.return_value = True

            store.get_principal_for_key(key_hash)

            # Should have been called at least once
            assert mock_compare.call_count >= 1

    def test_invalid_hash_returns_none_with_constant_time(self):
        """Verify that looking up non-existent hash returns None and uses constant-time."""
        store = InMemoryApiKeyStore()

        # Create some keys to ensure iteration happens
        for i in range(5):
            store.create_key(
                principal_id=f"principal-{i}",
                name=f"key-{i}",
            )

        # Look up a hash that doesn't exist
        with patch("mcp_hangar.infrastructure.auth.constant_time.hmac.compare_digest") as mock_compare:
            mock_compare.return_value = False  # Never matches

            result = store.get_principal_for_key("nonexistent_hash")

            # Should return None
            assert result is None

            # Should have called compare_digest for all stored keys
            assert mock_compare.call_count == 5
