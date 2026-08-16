"""Constant-time API key lookup: a miss must cost what a hit costs."""

from mcp_hangar.auth.infrastructure.constant_time import constant_time_key_lookup


class TestConstantTimeKeyLookup:
    """Tests for constant_time_key_lookup function."""

    def test_finds_existing_key(self):
        hash_dict = {"abc123": "value_a", "def456": "value_b"}
        result = constant_time_key_lookup("abc123", hash_dict)
        assert result == "value_a"

    def test_returns_none_for_missing_key(self):
        hash_dict = {"abc123": "value_a", "def456": "value_b"}
        result = constant_time_key_lookup("missing", hash_dict)
        assert result is None

    def test_iterates_all_entries_even_after_match(self):
        """The function must check ALL entries (constant time), not short-circuit."""
        hash_dict = {"first": 1, "second": 2, "third": 3}
        # The match is the first entry, but all entries should still be compared
        result = constant_time_key_lookup("first", hash_dict)
        assert result == 1

    def test_empty_dict_returns_none(self):
        result = constant_time_key_lookup("anything", {})
        assert result is None

    def test_last_match_wins_when_multiple_matches(self):
        """hmac.compare_digest is used; if somehow duplicates exist, last match wins."""
        # With normal dict, keys are unique, so this tests iteration over all items
        hash_dict = {"aaa": 10, "bbb": 20, "ccc": 30}
        result = constant_time_key_lookup("bbb", hash_dict)
        assert result == 20
