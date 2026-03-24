"""Tests for SQLiteToolAccessPolicyStore."""

import tempfile
from pathlib import Path

import pytest

from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from enterprise.auth.infrastructure.sqlite_tap_store import SQLiteToolAccessPolicyStore


class TestSQLiteToolAccessPolicyStore:
    """Tests for SQLiteToolAccessPolicyStore CRUD operations."""

    @pytest.fixture
    def store(self):
        """Create a temporary SQLiteToolAccessPolicyStore."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_tap.db"
            s = SQLiteToolAccessPolicyStore(db_path)
            yield s
            s.close()

    @pytest.fixture
    def db_path(self):
        """Yield a temporary db path for cross-instance tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "test_tap_persist.db"

    def test_get_policy_returns_none_when_absent(self, store):
        """get_policy returns None when no policy is stored."""
        result = store.get_policy("provider", "math")
        assert result is None

    def test_set_then_get_policy_provider(self, store):
        """set_policy + get_policy round-trip for provider scope."""
        store.set_policy("provider", "math", ["add", "sub"], ["mul"])

        policy = store.get_policy("provider", "math")

        assert policy is not None
        assert isinstance(policy, ToolAccessPolicy)
        assert "add" in policy.allow_list
        assert "sub" in policy.allow_list
        assert "mul" in policy.deny_list

    def test_set_then_get_policy_group(self, store):
        """set_policy + get_policy round-trip for group scope."""
        store.set_policy("group", "team-alpha", [], ["dangerous_tool"])

        policy = store.get_policy("group", "team-alpha")

        assert policy is not None
        assert policy.deny_list == ("dangerous_tool",)
        assert policy.allow_list == ()

    def test_set_then_get_policy_member(self, store):
        """set_policy + get_policy round-trip for member scope."""
        store.set_policy("member", "group1:user1", ["safe_tool"], [])

        policy = store.get_policy("member", "group1:user1")

        assert policy is not None
        assert "safe_tool" in policy.allow_list

    def test_clear_policy_removes_row(self, store):
        """clear_policy removes the stored policy."""
        store.set_policy("provider", "math", ["add"], [])
        assert store.get_policy("provider", "math") is not None

        store.clear_policy("provider", "math")

        assert store.get_policy("provider", "math") is None

    def test_clear_policy_noop_when_absent(self, store):
        """clear_policy on non-existent key does not raise."""
        store.clear_policy("provider", "nonexistent")  # Should not raise

    def test_list_all_policies_empty(self, store):
        """list_all_policies returns empty list when no policies exist."""
        result = store.list_all_policies()
        assert result == []

    def test_list_all_policies_returns_all_rows(self, store):
        """list_all_policies returns all stored policies as tuples."""
        store.set_policy("provider", "math", ["add"], [])
        store.set_policy("group", "team-alpha", [], ["bad_tool"])

        rows = store.list_all_policies()

        assert len(rows) == 2
        scopes = {r[0] for r in rows}
        assert "provider" in scopes
        assert "group" in scopes

    def test_list_all_policies_tuple_structure(self, store):
        """Each tuple is (scope, target_id, allow_list, deny_list)."""
        store.set_policy("provider", "calc", ["add", "sub"], ["rm"])

        rows = store.list_all_policies()
        assert len(rows) == 1

        scope, target_id, allow_list, deny_list = rows[0]
        assert scope == "provider"
        assert target_id == "calc"
        assert "add" in allow_list
        assert "sub" in allow_list
        assert "rm" in deny_list

    def test_set_policy_upsert_overwrites(self, store):
        """set_policy with same key overwrites the previous entry."""
        store.set_policy("provider", "math", ["add"], [])
        store.set_policy("provider", "math", ["mul"], ["div"])

        policy = store.get_policy("provider", "math")
        assert policy is not None
        assert list(policy.allow_list) == ["mul"]
        assert list(policy.deny_list) == ["div"]
        # Only one row in table
        assert len(store.list_all_policies()) == 1

    def test_persistence_across_instances(self, db_path):
        """Policies persist across store instances on the same db file."""
        store1 = SQLiteToolAccessPolicyStore(db_path)
        store1.set_policy("provider", "math", ["add"], [])
        store1.close()

        store2 = SQLiteToolAccessPolicyStore(db_path)
        policy = store2.get_policy("provider", "math")
        store2.close()

        assert policy is not None
        assert "add" in policy.allow_list
