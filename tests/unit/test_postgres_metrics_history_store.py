"""Tests for `PostgresMetricsHistoryStore` against a mocked `IConnectionFactory`.

No live PostgreSQL: the connection factory yields a `MagicMock` connection
whose cursor is also a `MagicMock`, so assertions are on the SQL text and
parameters the adapter sends, and on the Python-side return values -- never
on a real database.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

from mcp_hangar.domain.contracts.metrics_history import IMetricsHistoryStore
from mcp_hangar.infrastructure.persistence.backends.postgresql import metrics_history_store as _module
from mcp_hangar.infrastructure.persistence.backends.postgresql.metrics_history_store import (
    PostgresMetricsHistoryStore,
)
from mcp_hangar.infrastructure.persistence.metrics_history_store import MetricPoint


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


class TestIsolation:
    def test_implements_the_port(self):
        mock_conn, _mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn))

        assert isinstance(store, IMetricsHistoryStore)

    def test_module_does_not_import_psycopg2_directly(self):
        # The adapter knows SQL, not a driver -- `psycopg2` belongs to the
        # connection factory, not to this file. A stray `import psycopg2`
        # here would make the adapter untestable without a real driver
        # installed, exactly what the mocked `IConnectionFactory` exists to
        # avoid.
        assert "psycopg2" not in vars(_module)


class TestInit:
    def test_creates_table_on_init(self):
        mock_conn, mock_cursor = _make_conn()
        PostgresMetricsHistoryStore(_Factory(mock_conn))

        executed_sql = mock_cursor.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS metric_snapshots" in executed_sql
        assert "BIGSERIAL PRIMARY KEY" in executed_sql
        assert "CREATE INDEX IF NOT EXISTS idx_metric_snapshots_lookup" in executed_sql
        mock_conn.commit.assert_called_once()

    def test_default_retention_is_seven_days(self):
        mock_conn, _mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn))

        assert store._retention_days == 7

    def test_retention_days_is_configurable(self):
        mock_conn, _mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn), retention_days=30)

        assert store._retention_days == 30


class TestRecordSnapshot:
    def test_empty_list_does_not_touch_the_connection(self):
        mock_conn, mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn))
        mock_cursor.reset_mock()
        mock_conn.reset_mock()

        store.record_snapshot([])

        mock_cursor.executemany.assert_not_called()
        mock_conn.commit.assert_not_called()

    def test_records_a_batch_with_percent_s_placeholders(self):
        mock_conn, mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn))
        mock_cursor.reset_mock()
        mock_conn.reset_mock()

        points = [
            MetricPoint(mcp_server_id="srv-1", metric_name="tool_calls_total", value=3.0, recorded_at=100.0),
            MetricPoint(mcp_server_id="srv-1", metric_name="tool_calls_total", value=4.0, recorded_at=200.0),
        ]
        store.record_snapshot(points)

        args, _kwargs = mock_cursor.executemany.call_args
        executed_sql, rows = args
        assert "INSERT INTO metric_snapshots" in executed_sql
        assert "mcp_server_id, metric_name, value, recorded_at" in executed_sql
        assert "VALUES (%s, %s, %s, %s)" in executed_sql
        assert "?" not in executed_sql
        assert rows == [
            ("srv-1", "tool_calls_total", 3.0, 100.0),
            ("srv-1", "tool_calls_total", 4.0, 200.0),
        ]
        mock_conn.commit.assert_called_once()


class TestQuery:
    def test_no_filters_selects_everything(self):
        mock_conn, mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn))
        mock_cursor.reset_mock()
        mock_conn.reset_mock()
        mock_cursor.fetchall.return_value = []

        result = store.query()

        args, _kwargs = mock_cursor.execute.call_args
        executed_sql, params = args
        assert "WHERE" not in executed_sql
        assert "ORDER BY recorded_at ASC" in executed_sql
        assert "LIMIT %s" in executed_sql
        assert params == [1000]
        assert result == []

    def test_commits_to_release_the_read_transaction(self):
        # A `SELECT` still opens a transaction on the borrowed connection; a
        # borrowed-then-returned connection that never ends its transaction
        # sits "idle in transaction" for the next caller from the pool. The
        # SQLite reference's `_conn()` commits after every call, reads
        # included -- this adapter has to as well.
        mock_conn, mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn))
        mock_cursor.reset_mock()
        mock_conn.reset_mock()
        mock_cursor.fetchall.return_value = []

        store.query()

        mock_conn.commit.assert_called_once()

    def test_filters_build_a_where_clause_in_order(self):
        mock_conn, mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn))
        mock_cursor.reset_mock()
        mock_cursor.fetchall.return_value = []

        store.query(mcp_server_id="srv-1", metric_name="tool_calls_total", from_ts=10.0, to_ts=20.0, limit=5)

        args, _kwargs = mock_cursor.execute.call_args
        executed_sql, params = args
        assert "mcp_server_id = %s" in executed_sql
        assert "metric_name = %s" in executed_sql
        assert "recorded_at >= %s" in executed_sql
        assert "recorded_at <= %s" in executed_sql
        assert "?" not in executed_sql
        assert params == ["srv-1", "tool_calls_total", 10.0, 20.0, 5]

    def test_limit_is_clamped_to_ten_thousand(self):
        mock_conn, mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn))
        mock_cursor.reset_mock()
        mock_cursor.fetchall.return_value = []

        store.query(limit=50_000)

        params = mock_cursor.execute.call_args[0][1]
        assert params[-1] == 10_000

    def test_limit_below_one_is_clamped_to_one(self):
        mock_conn, mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn))
        mock_cursor.reset_mock()
        mock_cursor.fetchall.return_value = []

        store.query(limit=0)

        params = mock_cursor.execute.call_args[0][1]
        assert params[-1] == 1

    def test_returns_metric_points_ordered_as_selected(self):
        mock_conn, mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn))
        mock_cursor.reset_mock()
        mock_cursor.fetchall.return_value = [
            ("srv-1", "tool_calls_total", 3.0, 100.0),
            ("srv-1", "tool_calls_total", 4.0, 200.0),
        ]

        result = store.query(mcp_server_id="srv-1")

        assert result == [
            MetricPoint(mcp_server_id="srv-1", metric_name="tool_calls_total", value=3.0, recorded_at=100.0),
            MetricPoint(mcp_server_id="srv-1", metric_name="tool_calls_total", value=4.0, recorded_at=200.0),
        ]


class TestPrune:
    def test_deletes_rows_older_than_retention_and_returns_count(self):
        mock_conn, mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn), retention_days=7)
        mock_cursor.reset_mock()
        mock_conn.reset_mock()
        mock_cursor.rowcount = 3

        result = store.prune()

        assert result == 3
        executed_sql, params = mock_cursor.execute.call_args[0]
        assert "DELETE FROM metric_snapshots WHERE recorded_at < %s" in executed_sql
        assert "?" not in executed_sql
        assert len(params) == 1
        mock_conn.commit.assert_called_once()

    def test_returns_zero_when_rowcount_is_falsy(self):
        mock_conn, mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn))
        mock_cursor.rowcount = 0

        result = store.prune()

        assert result == 0

    def test_returns_zero_when_rowcount_is_none(self):
        mock_conn, mock_cursor = _make_conn()
        store = PostgresMetricsHistoryStore(_Factory(mock_conn))
        mock_cursor.rowcount = None

        result = store.prune()

        assert result == 0
