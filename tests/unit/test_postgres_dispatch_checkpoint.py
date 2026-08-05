"""Tests for `PostgresDispatchCheckpoint` against a mocked `IConnectionFactory`.

No live PostgreSQL: the connection factory yields a `MagicMock` connection
whose cursor is also a `MagicMock`, so assertions are on the SQL text and
parameters the adapter sends, and on the Python-side return values -- never
on a real database.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

from mcp_hangar.infrastructure.persistence.backends.postgresql.dispatch_checkpoint import (
    PostgresDispatchCheckpoint,
)


class _Factory:
    """The port, not a bare callable: the adapter depends on `IConnectionFactory`,
    so the double has to be one too -- otherwise the test passes against a shape
    production does not use.
    """

    def __init__(self, conn):
        self._conn = conn

    @contextmanager
    def get_connection(self):
        yield self._conn


def _make_conn():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


class TestInit:
    def test_creates_table_on_init(self):
        mock_conn, mock_cursor = _make_conn()
        PostgresDispatchCheckpoint(_Factory(mock_conn))

        executed_sql = mock_cursor.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS dispatch_checkpoint" in executed_sql
        assert "id = 0" in executed_sql
        mock_conn.commit.assert_called_once()


class TestRead:
    def test_read_returns_zero_when_no_row(self):
        mock_conn, mock_cursor = _make_conn()
        checkpoint = PostgresDispatchCheckpoint(_Factory(mock_conn))
        mock_cursor.reset_mock()
        mock_conn.reset_mock()
        mock_cursor.fetchone.return_value = None

        result = checkpoint.read()

        assert result == 0
        executed_sql = mock_cursor.execute.call_args[0][0]
        assert "SELECT position FROM dispatch_checkpoint WHERE id = 0" in executed_sql

    def test_read_returns_stored_position(self):
        mock_conn, mock_cursor = _make_conn()
        checkpoint = PostgresDispatchCheckpoint(_Factory(mock_conn))
        mock_cursor.fetchone.return_value = (42,)

        result = checkpoint.read()

        assert result == 42
        assert isinstance(result, int)

    def test_read_coerces_to_int(self):
        mock_conn, mock_cursor = _make_conn()
        checkpoint = PostgresDispatchCheckpoint(_Factory(mock_conn))
        mock_cursor.fetchone.return_value = ("17",)

        result = checkpoint.read()

        assert result == 17
        assert isinstance(result, int)


class TestAdvance:
    def test_advance_uses_upsert_with_greatest(self):
        mock_conn, mock_cursor = _make_conn()
        checkpoint = PostgresDispatchCheckpoint(_Factory(mock_conn))
        mock_cursor.reset_mock()
        mock_conn.reset_mock()

        checkpoint.advance(10)

        args, _kwargs = mock_cursor.execute.call_args
        executed_sql, params = args
        assert "INSERT INTO dispatch_checkpoint (id, position) VALUES (0, %s)" in executed_sql
        assert "ON CONFLICT (id) DO UPDATE SET" in executed_sql
        assert "GREATEST(dispatch_checkpoint.position, excluded.position)" in executed_sql
        assert params == (10,)
        mock_conn.commit.assert_called_once()

    def test_advance_uses_percent_s_placeholder_not_question_mark(self):
        mock_conn, mock_cursor = _make_conn()
        checkpoint = PostgresDispatchCheckpoint(_Factory(mock_conn))
        mock_cursor.reset_mock()

        checkpoint.advance(5)

        executed_sql = mock_cursor.execute.call_args[0][0]
        assert "?" not in executed_sql

    def test_advance_commits(self):
        mock_conn, mock_cursor = _make_conn()
        checkpoint = PostgresDispatchCheckpoint(_Factory(mock_conn))
        mock_conn.reset_mock()

        checkpoint.advance(1)

        mock_conn.commit.assert_called_once()


class TestNeverGoesBackwards:
    """The mark must never move backwards -- a caller delivering an older batch
    after a newer one must not undo the newer position. This is enforced by the
    SQL (GREATEST in the ON CONFLICT clause), so these tests pin the SQL shape
    that guarantees it, since the mock cursor does not evaluate SQL itself.
    """

    def test_advance_statement_is_a_single_upsert_not_read_then_write(self):
        mock_conn, mock_cursor = _make_conn()
        checkpoint = PostgresDispatchCheckpoint(_Factory(mock_conn))
        mock_cursor.reset_mock()
        mock_conn.reset_mock()

        checkpoint.advance(99)

        # Exactly one execute call: no separate SELECT before the write, so
        # there is no window for a concurrent advance to be clobbered.
        assert mock_cursor.execute.call_count == 1

    def test_conflict_target_is_the_singleton_id(self):
        mock_conn, mock_cursor = _make_conn()
        checkpoint = PostgresDispatchCheckpoint(_Factory(mock_conn))
        mock_cursor.reset_mock()

        checkpoint.advance(3)

        executed_sql = mock_cursor.execute.call_args[0][0]
        assert "ON CONFLICT (id)" in executed_sql
