"""Unit tests for PostgresToolAccessPolicyStore.

Runs entirely against a mocked `IConnectionFactory` -- no live PostgreSQL is
used or required. Asserts on the SQL text executed (table names, ON CONFLICT
clause, placeholders) and on Python-side behaviour, mirroring how
`sqlite_tap_store.py` is exercised and how the mocked-cursor pattern is used
in `test_auth_coverage_batch4.py`.
"""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.infrastructure.persistence.backends.postgresql.tool_access_policy_store import (
    PostgresToolAccessPolicyStore,
)


class _NullFactory:
    """A factory that yields nothing, for tests that never touch a connection."""

    @contextmanager
    def get_connection(self):
        yield None


def _make_store(table_prefix: str = ""):
    """Build a store wired to a mocked connection/cursor pair.

    The double is shaped like `IConnectionFactory` (a `get_connection()`
    contextmanager), not a bare callable, so the test exercises the same
    contract production code depends on.
    """
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)

    class _Factory:
        @contextmanager
        def get_connection(self):
            yield mock_conn

    store = PostgresToolAccessPolicyStore(connection_factory=_Factory(), table_prefix=table_prefix)
    return store, mock_conn, mock_cursor


class TestInit:
    def test_default_table_name(self):
        store = PostgresToolAccessPolicyStore(connection_factory=_NullFactory())
        assert store._table == "tool_access_policies"

    def test_table_name_with_prefix(self):
        store = PostgresToolAccessPolicyStore(connection_factory=_NullFactory(), table_prefix="auth_")
        assert store._table == "auth_tool_access_policies"


class TestInitialize:
    def test_creates_schema_and_commits(self):
        store, mock_conn, mock_cursor = _make_store()
        store.initialize()
        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS tool_access_policies" in sql
        assert "PRIMARY KEY (scope, target_id)" in sql
        mock_conn.commit.assert_called_once()

    def test_with_prefix_uses_prefixed_table_name(self):
        store, mock_conn, mock_cursor = _make_store(table_prefix="myprefix_")
        store.initialize()
        sql = mock_cursor.execute.call_args[0][0]
        assert "myprefix_tool_access_policies" in sql
        assert "CREATE TABLE IF NOT EXISTS tool_access_policies" not in sql


class TestSetPolicy:
    def test_upserts_with_on_conflict_and_json_encoded_lists(self):
        store, mock_conn, mock_cursor = _make_store()
        store.set_policy("mcp_server", "srv-1", ["read_*"], ["delete_*"])

        mock_cursor.execute.assert_called_once()
        sql, params = mock_cursor.execute.call_args[0]
        assert "INSERT INTO tool_access_policies" in sql
        assert "%s" in sql
        assert "?" not in sql
        assert "ON CONFLICT (scope, target_id) DO UPDATE SET" in sql
        assert params[0] == "mcp_server"
        assert params[1] == "srv-1"
        assert json.loads(params[2]) == ["read_*"]
        assert json.loads(params[3]) == ["delete_*"]
        mock_conn.commit.assert_called_once()

    def test_uses_prefixed_table_name(self):
        store, mock_conn, mock_cursor = _make_store(table_prefix="auth_")
        store.set_policy("group", "grp-1", [], [])
        sql = mock_cursor.execute.call_args[0][0]
        assert "auth_tool_access_policies" in sql

    def test_empty_lists_round_trip_as_empty_json_arrays(self):
        store, mock_conn, mock_cursor = _make_store()
        store.set_policy("member", "user-1", [], [])
        _, params = mock_cursor.execute.call_args[0]
        assert params[2] == "[]"
        assert params[3] == "[]"


class TestGetPolicy:
    def test_missing_policy_returns_none(self):
        store, mock_conn, mock_cursor = _make_store()
        mock_cursor.fetchone.return_value = None
        result = store.get_policy("mcp_server", "does-not-exist")
        assert result is None

    def test_found_policy_returns_tool_access_policy_with_tuples(self):
        store, mock_conn, mock_cursor = _make_store()
        mock_cursor.fetchone.return_value = (["read_*"], ["delete_*"])
        result = store.get_policy("mcp_server", "srv-1")
        assert isinstance(result, ToolAccessPolicy)
        assert result.allow_list == ("read_*",)
        assert result.deny_list == ("delete_*",)

    def test_decodes_json_string_columns_defensively(self):
        """A raw/mocked cursor may hand back JSON text instead of a decoded
        object even though psycopg2 normally decodes JSONB automatically."""
        store, mock_conn, mock_cursor = _make_store()
        mock_cursor.fetchone.return_value = (json.dumps(["a", "b"]), json.dumps(["c"]))
        result = store.get_policy("group", "grp-1")
        assert result.allow_list == ("a", "b")
        assert result.deny_list == ("c",)

    def test_query_uses_placeholders_and_where_clause(self):
        store, mock_conn, mock_cursor = _make_store()
        mock_cursor.fetchone.return_value = None
        store.get_policy("mcp_server", "srv-1")
        sql, params = mock_cursor.execute.call_args[0]
        assert "SELECT allow_list, deny_list" in sql
        assert "WHERE scope = %s AND target_id = %s" in sql
        assert params == ("mcp_server", "srv-1")


class TestClearPolicy:
    def test_deletes_by_scope_and_target(self):
        store, mock_conn, mock_cursor = _make_store()
        store.clear_policy("mcp_server", "srv-1")
        sql, params = mock_cursor.execute.call_args[0]
        assert "DELETE FROM tool_access_policies" in sql
        assert "WHERE scope = %s AND target_id = %s" in sql
        assert params == ("mcp_server", "srv-1")
        mock_conn.commit.assert_called_once()

    def test_uses_prefixed_table_name(self):
        store, mock_conn, mock_cursor = _make_store(table_prefix="auth_")
        store.clear_policy("member", "user-1")
        sql = mock_cursor.execute.call_args[0][0]
        assert "auth_tool_access_policies" in sql


class TestListAllPolicies:
    def test_empty_table_returns_empty_list(self):
        store, mock_conn, mock_cursor = _make_store()
        mock_cursor.fetchall.return_value = []
        result = store.list_all_policies()
        assert result == []

    def test_returns_tuples_with_decoded_lists(self):
        store, mock_conn, mock_cursor = _make_store()
        mock_cursor.fetchall.return_value = [
            ("mcp_server", "srv-1", ["read_*"], ["delete_*"]),
            ("group", "grp-1", [], []),
        ]
        result = store.list_all_policies()
        assert result == [
            ("mcp_server", "srv-1", ["read_*"], ["delete_*"]),
            ("group", "grp-1", [], []),
        ]

    def test_decodes_json_string_columns_defensively(self):
        store, mock_conn, mock_cursor = _make_store()
        mock_cursor.fetchall.return_value = [
            ("member", "user-1", json.dumps(["a"]), json.dumps(["b"])),
        ]
        result = store.list_all_policies()
        assert result == [("member", "user-1", ["a"], ["b"])]

    def test_selects_all_four_columns(self):
        store, mock_conn, mock_cursor = _make_store()
        mock_cursor.fetchall.return_value = []
        store.list_all_policies()
        sql = mock_cursor.execute.call_args[0][0]
        assert "SELECT scope, target_id, allow_list, deny_list" in sql
        assert "FROM tool_access_policies" in sql


class TestPoolHygieneOnError:
    """A pooled connection is returned to the pool (`putconn`) by
    `IConnectionFactory.get_connection()`'s `finally` clause regardless of
    transaction state. If a write raises mid-transaction and nothing rolls
    back, the *next* caller to borrow that connection -- potentially a
    concurrent writer on a different request -- inherits an aborted
    transaction and every statement it runs fails until someone rolls back.
    Each write path must roll back before propagating.
    """

    def test_set_policy_rolls_back_and_reraises_on_execute_failure(self):
        store, mock_conn, mock_cursor = _make_store()
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            store.set_policy("mcp_server", "srv-1", ["read_*"], [])

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

    def test_clear_policy_rolls_back_and_reraises_on_execute_failure(self):
        store, mock_conn, mock_cursor = _make_store()
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            store.clear_policy("mcp_server", "srv-1")

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

    def test_initialize_rolls_back_and_reraises_on_execute_failure(self):
        store, mock_conn, mock_cursor = _make_store()
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            store.initialize()

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

    def test_set_policy_success_path_never_rolls_back(self):
        store, mock_conn, mock_cursor = _make_store()
        store.set_policy("mcp_server", "srv-1", [], [])
        mock_conn.rollback.assert_not_called()
        mock_conn.commit.assert_called_once()
