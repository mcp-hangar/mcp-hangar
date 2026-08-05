"""Unit tests for PostgresSagaStateStore.

Runs entirely against a mocked `IConnectionFactory` -- no live PostgreSQL. The
mock connection is restricted to the psycopg2-shaped surface (`cursor`,
`commit`) via `spec`, which matters here specifically: `MigrationRunner`
branches on `hasattr(conn, "executescript")` to tell SQLite and PostgreSQL
connections apart, and a bare `MagicMock()` auto-creates *any* attribute you
ask it for -- including `executescript` -- which would silently steer every
test down the SQLite migration path instead of the PostgreSQL one under test.
"""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.infrastructure.persistence.backends.postgresql.saga_state_store import (
    SAGA_STORE_MIGRATIONS,
    PostgresSagaStateStore,
)


def _make_conn_and_cursor():
    """A mock psycopg2 connection/cursor pair.

    `cursor()` returns the same mock whether called directly (as
    `MigrationRunner` does) or via `with conn.cursor() as cur:` (as the store
    itself does), so assertions don't have to care which style produced a
    given `execute()` call.
    """
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = Mock(return_value=mock_cursor)
    mock_cursor.__exit__ = Mock(return_value=False)
    # fetchone() defaults to a truthy MagicMock, which would break the
    # `row[0]` version check inside MigrationRunner during __init__; a real
    # "no migrations applied yet" row is (0,).
    mock_cursor.fetchone.return_value = (0,)

    # spec restricts the connection to a psycopg2-like surface so
    # `hasattr(conn, "executescript")` is False, as it would be for a real
    # psycopg2 connection.
    mock_conn = MagicMock(spec=["cursor", "commit", "rollback"])
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor


class _Factory:
    """The port, not a bare callable -- the store depends on
    `IConnectionFactory`, so the double has to be one too."""

    def __init__(self, conn):
        self._conn = conn

    @contextmanager
    def get_connection(self):
        yield self._conn


@pytest.fixture
def store_conn_cursor():
    """A PostgresSagaStateStore wired to a mock connection/cursor pair.

    Migrations run during __init__ (mirroring the SQLite reference), so the
    mocks are reset after construction to give each test a clean slate.
    """
    mock_conn, mock_cursor = _make_conn_and_cursor()
    factory = _Factory(mock_conn)
    store = PostgresSagaStateStore(connection_factory=factory)
    mock_cursor.reset_mock()
    mock_conn.reset_mock()
    mock_cursor.fetchone.return_value = (0,)
    return store, mock_conn, mock_cursor


class TestInit:
    def test_runs_migrations_on_construction(self):
        mock_conn, mock_cursor = _make_conn_and_cursor()
        factory = _Factory(mock_conn)

        PostgresSagaStateStore(connection_factory=factory)

        assert mock_cursor.execute.called
        assert mock_conn.commit.called

    def test_migration_creates_tables_via_cursor_not_executescript(self):
        mock_conn, mock_cursor = _make_conn_and_cursor()
        factory = _Factory(mock_conn)

        PostgresSagaStateStore(connection_factory=factory)

        executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert any("CREATE TABLE IF NOT EXISTS saga_state" in sql for sql in executed_sql)
        assert any("CREATE TABLE IF NOT EXISTS saga_idempotency" in sql for sql in executed_sql)

    def test_migration_sql_uses_jsonb_for_state_data(self):
        sql = SAGA_STORE_MIGRATIONS[0]["sql"]
        assert "state_data JSONB NOT NULL" in sql
        assert "PRIMARY KEY (saga_type, saga_id)" in sql
        assert "PRIMARY KEY (saga_type, event_position)" in sql


class TestCheckpoint:
    def test_executes_upsert_with_on_conflict_do_update(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor

        store.checkpoint("order_saga", "saga-1", {"step": 2}, 42)

        sql, params = mock_cursor.execute.call_args[0]
        assert "INSERT INTO saga_state" in sql
        assert "%s" in sql
        assert "?" not in sql
        assert "ON CONFLICT (saga_type, saga_id) DO UPDATE SET" in sql
        assert "state_data = EXCLUDED.state_data" in sql
        assert "last_event_position = EXCLUDED.last_event_position" in sql

        saga_type, saga_id, state_data_json, last_event_position, updated_at = params
        assert saga_type == "order_saga"
        assert saga_id == "saga-1"
        assert json.loads(state_data_json) == {"step": 2}
        assert last_event_position == 42
        assert isinstance(updated_at, str)

    def test_commits(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor

        store.checkpoint("order_saga", "saga-1", {}, 0)

        mock_conn.commit.assert_called_once()

    def test_overwrites_state_for_same_type_and_id(self, store_conn_cursor):
        """checkpoint() is a save-latest operation, not an append -- calling
        it twice for the same saga_type + saga_id must not error or grow
        state, matching the reference's INSERT OR REPLACE semantics."""
        store, mock_conn, mock_cursor = store_conn_cursor

        store.checkpoint("order_saga", "saga-1", {"step": 1}, 1)
        store.checkpoint("order_saga", "saga-1", {"step": 2}, 2)

        assert mock_cursor.execute.call_count == 2
        assert mock_conn.commit.call_count == 2


class TestLoad:
    def test_returns_none_when_no_row(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor
        mock_cursor.fetchone.return_value = None

        result = store.load("order_saga")

        assert result is None

    def test_uses_percent_s_placeholder(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor
        mock_cursor.fetchone.return_value = None

        store.load("order_saga")

        sql, params = mock_cursor.execute.call_args[0]
        assert sql == "SELECT state_data, last_event_position FROM saga_state WHERE saga_type = %s"
        assert params == ("order_saga",)

    def test_decodes_jsonb_dict_returned_by_driver(self, store_conn_cursor):
        """psycopg2 commonly decodes JSONB columns to a dict already."""
        store, mock_conn, mock_cursor = store_conn_cursor
        mock_cursor.fetchone.return_value = ({"step": 5}, 100)

        result = store.load("order_saga")

        assert result == {"state_data": {"step": 5}, "last_event_position": 100}

    def test_decodes_jsonb_string_fallback(self, store_conn_cursor):
        """Falls back to json.loads if the driver hands back a raw string."""
        store, mock_conn, mock_cursor = store_conn_cursor
        mock_cursor.fetchone.return_value = (json.dumps({"step": 5}), 100)

        result = store.load("order_saga")

        assert result == {"state_data": {"step": 5}, "last_event_position": 100}


class TestMarkProcessed:
    def test_executes_insert_with_on_conflict_do_nothing(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor

        store.mark_processed("order_saga", 7)

        sql, params = mock_cursor.execute.call_args[0]
        assert "INSERT INTO saga_idempotency" in sql
        assert "ON CONFLICT (saga_type, event_position) DO NOTHING" in sql
        assert "%s" in sql
        saga_type, event_position, processed_at = params
        assert saga_type == "order_saga"
        assert event_position == 7
        assert isinstance(processed_at, str)

    def test_commits(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor

        store.mark_processed("order_saga", 7)

        mock_conn.commit.assert_called_once()

    def test_duplicate_calls_do_not_raise(self, store_conn_cursor):
        """ON CONFLICT DO NOTHING is what makes duplicate delivery safe --
        the mock can't enforce the constraint, but the call itself must not
        require a first check-then-insert dance in the store."""
        store, mock_conn, mock_cursor = store_conn_cursor

        store.mark_processed("order_saga", 7)
        store.mark_processed("order_saga", 7)

        assert mock_cursor.execute.call_count == 2


class TestPoolHygieneOnError:
    """A pooled connection is returned to the pool (`putconn`) by
    `IConnectionFactory.get_connection()`'s `finally` clause regardless of
    transaction state. If a statement raises mid-transaction and nothing
    rolls back, the *next* caller to borrow that connection -- potentially a
    concurrent writer on a different saga -- inherits an aborted transaction
    and every statement it runs fails until someone rolls back. Each method
    must roll back before propagating.
    """

    def test_checkpoint_rolls_back_and_reraises_on_execute_failure(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            store.checkpoint("order_saga", "saga-1", {"step": 1}, 1)

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

    def test_load_rolls_back_and_reraises_on_execute_failure(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            store.load("order_saga")

        mock_conn.rollback.assert_called_once()

    def test_mark_processed_rolls_back_and_reraises_on_execute_failure(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            store.mark_processed("order_saga", 7)

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

    def test_is_processed_rolls_back_and_reraises_on_execute_failure(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            store.is_processed("order_saga", 7)

        mock_conn.rollback.assert_called_once()

    def test_checkpoint_success_path_never_rolls_back(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor

        store.checkpoint("order_saga", "saga-1", {"step": 1}, 1)

        mock_conn.rollback.assert_not_called()
        mock_conn.commit.assert_called_once()


class TestIsProcessed:
    def test_true_when_row_found(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor
        mock_cursor.fetchone.return_value = (1,)

        assert store.is_processed("order_saga", 7) is True

    def test_false_when_no_row(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor
        mock_cursor.fetchone.return_value = None

        assert store.is_processed("order_saga", 7) is False

    def test_uses_percent_s_placeholders(self, store_conn_cursor):
        store, mock_conn, mock_cursor = store_conn_cursor
        mock_cursor.fetchone.return_value = None

        store.is_processed("order_saga", 7)

        sql, params = mock_cursor.execute.call_args[0]
        assert "WHERE saga_type = %s AND event_position = %s" in sql
        assert params == ("order_saga", 7)
