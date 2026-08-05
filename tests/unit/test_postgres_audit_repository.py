"""Unit tests for PostgresAuditRepository.

Runs entirely against a mocked `IConnectionFactory` -- no live PostgreSQL is
used or required. Asserts on the SQL text executed (table names, placeholders,
filter clauses) and on Python-side behaviour (masking, JSON decode, error
wrapping), mirroring how `audit_repository.py`'s SQLite class is exercised and
how the mocked-cursor pattern is used in `test_auth_coverage_batch4.py` /
`test_postgres_tool_access_policy_store.py`.
"""

import json
import threading
from contextlib import contextmanager
from datetime import datetime, UTC
from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.domain.contracts.persistence import AuditAction, AuditEntry, PersistenceError
from mcp_hangar.infrastructure.persistence.backends.postgresql.audit_repository import (
    PostgresAuditRepository,
)


def _make_repo(table_prefix: str = ""):
    """Build a repository wired to a mocked connection/cursor pair.

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

    repo = PostgresAuditRepository(connection_factory=_Factory(), table_prefix=table_prefix)
    # The constructor issues its own schema-creation call; tests assert on
    # calls made by the method under test, so reset the mock's call history.
    mock_cursor.reset_mock()
    mock_conn.reset_mock()
    return repo, mock_conn, mock_cursor


def _entry(**overrides) -> AuditEntry:
    defaults = {
        "entity_id": "mcp-server-1",
        "entity_type": "mcp_server",
        "action": AuditAction.CREATED,
        "timestamp": datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC),
        "actor": "alice",
    }
    defaults.update(overrides)
    return AuditEntry(**defaults)


class TestInit:
    def test_default_table_name(self):
        repo, mock_conn, mock_cursor = _make_repo()
        assert repo._table == "audit_log"

    def test_table_name_with_prefix(self):
        repo, _, _ = _make_repo(table_prefix="myapp_")
        assert repo._table == "myapp_audit_log"

    def test_constructor_creates_schema_and_commits(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)

        class _Factory:
            @contextmanager
            def get_connection(self):
                yield mock_conn

        PostgresAuditRepository(connection_factory=_Factory())
        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS audit_log" in sql
        assert "BIGSERIAL PRIMARY KEY" in sql
        mock_conn.commit.assert_called_once()

    def test_constructor_uses_prefixed_table_name_in_schema(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)

        class _Factory:
            @contextmanager
            def get_connection(self):
                yield mock_conn

        PostgresAuditRepository(connection_factory=_Factory(), table_prefix="auth_")
        sql = mock_cursor.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS auth_audit_log" in sql

    def test_schema_creation_failure_rolls_back_before_releasing_the_connection(self):
        """Same pool-poisoning concern as every query method: a failed
        CREATE TABLE/INDEX must not return the connection to the pool still
        in an aborted transaction."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)
        mock_cursor.execute.side_effect = RuntimeError("relation already exists, differently")

        class _Factory:
            @contextmanager
            def get_connection(self):
                yield mock_conn

        with pytest.raises(RuntimeError):
            PostgresAuditRepository(connection_factory=_Factory())

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()


class TestAppend:
    async def test_inserts_with_placeholders_and_serialized_json(self):
        repo, mock_conn, mock_cursor = _make_repo()
        entry = _entry(
            old_state={"status": "stopped"},
            new_state={"status": "running"},
            metadata={"reason": "manual"},
            correlation_id="corr-1",
            caller_user_id="user-1",
            caller_agent_id="agent-1",
            caller_session_id="session-1",
            caller_principal_type="user",
        )

        await repo.append(entry)

        mock_cursor.execute.assert_called_once()
        sql, params = mock_cursor.execute.call_args[0]
        assert "INSERT INTO audit_log" in sql
        assert "%s" in sql
        assert "?" not in sql
        assert params[0] == "mcp-server-1"
        assert params[1] == "mcp_server"
        assert params[2] == "created"
        assert params[3] == "alice"
        assert params[4] == entry.timestamp.isoformat()
        assert json.loads(params[5]) == {"status": "stopped"}
        assert json.loads(params[6]) == {"status": "running"}
        assert json.loads(params[7]) == {"reason": "manual"}
        assert params[8] == "corr-1"
        assert params[9] == "user-1"
        assert params[10] == "agent-1"
        assert params[11] == "session-1"
        assert params[12] == "user"
        mock_conn.commit.assert_called_once()

    async def test_uses_prefixed_table_name(self):
        repo, _, mock_cursor = _make_repo(table_prefix="auth_")
        await repo.append(_entry())
        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO auth_audit_log" in sql

    async def test_none_old_and_new_state_stored_as_none(self):
        repo, _, mock_cursor = _make_repo()
        await repo.append(_entry(old_state=None, new_state=None, metadata={}))
        _, params = mock_cursor.execute.call_args[0]
        assert params[5] is None
        assert params[6] is None
        assert params[7] is None

    async def test_secrets_in_old_and_new_state_are_masked(self):
        repo, _, mock_cursor = _make_repo()
        entry = _entry(
            old_state={"api_key": "sk-super-secret-value"},
            new_state={"api_key": "sk-another-secret-value"},
        )
        await repo.append(entry)
        _, params = mock_cursor.execute.call_args[0]
        old_stored = json.loads(params[5])
        new_stored = json.loads(params[6])
        assert old_stored["api_key"] != "sk-super-secret-value"
        assert new_stored["api_key"] != "sk-another-secret-value"

    async def test_failure_is_wrapped_as_persistence_error(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("connection reset")

        with pytest.raises(PersistenceError, match="Failed to append audit entry"):
            await repo.append(_entry())

    async def test_failure_rolls_back_before_releasing_the_connection(self):
        """A pooled connection factory returns the connection to the pool in
        a bare `finally` regardless of transaction state -- if a failed
        INSERT isn't rolled back first, the connection goes back into the
        pool aborted, and poisons whichever unrelated caller borrows it next.
        """
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("connection reset")

        with pytest.raises(PersistenceError):
            await repo.append(_entry())

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

    async def test_runs_off_the_calling_thread(self):
        """`append` is declared `async def` to match the port, but psycopg2
        is a blocking driver -- the SQL round-trip must happen on a worker
        thread (e.g. via `asyncio.to_thread`) or awaiting it stalls the
        entire event loop for the duration of the query, unlike the
        reference's genuinely-async aiosqlite connection.
        """
        repo, _, mock_cursor = _make_repo()
        calling_thread = threading.get_ident()
        seen_thread = {}

        def _record_thread(*args, **kwargs):
            seen_thread["ident"] = threading.get_ident()

        mock_cursor.execute.side_effect = _record_thread

        await repo.append(_entry())

        assert seen_thread["ident"] != calling_thread


class TestGetByEntity:
    async def test_without_entity_type_filters_only_by_entity_id(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []
        await repo.get_by_entity("mcp-server-1")
        sql, params = mock_cursor.execute.call_args[0]
        assert "WHERE entity_id = %s" in sql
        assert "entity_type" not in sql.split("WHERE")[1].split("ORDER")[0]
        assert params == ("mcp-server-1", 100, 0)

    async def test_with_entity_type_adds_filter_and_params(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []
        await repo.get_by_entity("mcp-server-1", entity_type="mcp_server", limit=10, offset=5)
        sql, params = mock_cursor.execute.call_args[0]
        assert "WHERE entity_id = %s AND entity_type = %s" in sql
        assert params == ("mcp-server-1", "mcp_server", 10, 5)

    async def test_orders_newest_first(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []
        await repo.get_by_entity("mcp-server-1")
        sql = mock_cursor.execute.call_args[0][0]
        assert "ORDER BY timestamp DESC" in sql

    async def test_rows_are_decoded_into_audit_entries(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = [
            (
                "mcp-server-1",
                "mcp_server",
                "created",
                "alice",
                "2026-08-06T12:00:00+00:00",
                json.dumps({"a": 1}),
                json.dumps({"b": 2}),
                json.dumps({"c": 3}),
                "corr-1",
                "user-1",
                "agent-1",
                "session-1",
                "user",
            )
        ]
        result = await repo.get_by_entity("mcp-server-1")
        assert len(result) == 1
        entry = result[0]
        assert isinstance(entry, AuditEntry)
        assert entry.entity_id == "mcp-server-1"
        assert entry.action == AuditAction.CREATED
        assert entry.timestamp == datetime.fromisoformat("2026-08-06T12:00:00+00:00")
        assert entry.old_state == {"a": 1}
        assert entry.new_state == {"b": 2}
        assert entry.metadata == {"c": 3}
        assert entry.correlation_id == "corr-1"
        assert entry.caller_user_id == "user-1"

    async def test_already_decoded_jsonb_columns_are_passed_through(self):
        """psycopg2 normally hands back JSONB columns already decoded to
        dict/list -- the row-to-entry conversion must not double-decode."""
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = [
            (
                "mcp-server-1",
                "mcp_server",
                "created",
                "alice",
                "2026-08-06T12:00:00+00:00",
                {"a": 1},
                {"b": 2},
                {"c": 3},
                None,
                None,
                None,
                None,
                None,
            )
        ]
        result = await repo.get_by_entity("mcp-server-1")
        assert result[0].old_state == {"a": 1}
        assert result[0].new_state == {"b": 2}
        assert result[0].metadata == {"c": 3}

    async def test_missing_metadata_defaults_to_empty_dict(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = [
            (
                "mcp-server-1",
                "mcp_server",
                "created",
                "alice",
                "2026-08-06T12:00:00+00:00",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
        ]
        result = await repo.get_by_entity("mcp-server-1")
        assert result[0].old_state is None
        assert result[0].new_state is None
        assert result[0].metadata == {}

    async def test_empty_result_returns_empty_list(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []
        result = await repo.get_by_entity("does-not-exist")
        assert result == []

    async def test_failure_is_wrapped_as_persistence_error(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")
        with pytest.raises(PersistenceError, match="Failed to get audit entries by entity"):
            await repo.get_by_entity("mcp-server-1")

    async def test_failure_rolls_back_before_releasing_the_connection(self):
        """Reads abort the transaction on failure too -- a SELECT that
        raises must roll back before the connection is returned to the pool,
        the same as a failed write, or it poisons the next borrower."""
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")
        with pytest.raises(PersistenceError):
            await repo.get_by_entity("mcp-server-1")
        mock_conn.rollback.assert_called_once()


class TestGetByTimeRange:
    async def test_base_query_filters_by_timestamp_between(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []
        start = datetime(2026, 8, 1, tzinfo=UTC)
        end = datetime(2026, 8, 6, tzinfo=UTC)
        await repo.get_by_time_range(start, end)
        sql, params = mock_cursor.execute.call_args[0]
        assert "WHERE timestamp BETWEEN %s AND %s" in sql
        assert params[0] == start.isoformat()
        assert params[1] == end.isoformat()
        assert params[-1] == 1000  # default limit

    async def test_entity_type_and_action_filters_append_clauses_and_params(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []
        start = datetime(2026, 8, 1, tzinfo=UTC)
        end = datetime(2026, 8, 6, tzinfo=UTC)
        await repo.get_by_time_range(start, end, entity_type="mcp_server", action=AuditAction.STOPPED, limit=50)
        sql, params = mock_cursor.execute.call_args[0]
        assert "AND entity_type = %s" in sql
        assert "AND action = %s" in sql
        assert params == [start.isoformat(), end.isoformat(), "mcp_server", "stopped", 50]

    async def test_failure_is_wrapped_as_persistence_error(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")
        with pytest.raises(PersistenceError, match="Failed to get audit entries by time range"):
            await repo.get_by_time_range(datetime.now(UTC), datetime.now(UTC))


class TestGetByCorrelationId:
    async def test_orders_ascending_by_timestamp(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []
        await repo.get_by_correlation_id("corr-1")
        sql, params = mock_cursor.execute.call_args[0]
        assert "WHERE correlation_id = %s" in sql
        assert "ORDER BY timestamp ASC" in sql
        assert params == ("corr-1",)

    async def test_failure_is_wrapped_as_persistence_error(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")
        with pytest.raises(PersistenceError, match="Failed to get audit entries by correlation"):
            await repo.get_by_correlation_id("corr-1")


class TestCountByEntity:
    async def test_without_entity_type(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = (7,)
        result = await repo.count_by_entity("mcp-server-1")
        assert result == 7
        sql, params = mock_cursor.execute.call_args[0]
        assert "WHERE entity_id = %s" in sql
        assert "entity_type" not in sql
        assert params == ("mcp-server-1",)

    async def test_with_entity_type(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = (3,)
        result = await repo.count_by_entity("mcp-server-1", entity_type="mcp_server")
        assert result == 3
        sql, params = mock_cursor.execute.call_args[0]
        assert "WHERE entity_id = %s AND entity_type = %s" in sql
        assert params == ("mcp-server-1", "mcp_server")

    async def test_no_row_returns_zero(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = None
        result = await repo.count_by_entity("mcp-server-1")
        assert result == 0

    async def test_failure_is_wrapped_as_persistence_error(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")
        with pytest.raises(PersistenceError, match="Failed to count audit entries"):
            await repo.count_by_entity("mcp-server-1")


class TestGetRecentActions:
    async def test_filters_by_entity_type_and_action(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []
        await repo.get_recent_actions("mcp_server", AuditAction.STARTED, limit=25)
        sql, params = mock_cursor.execute.call_args[0]
        assert "WHERE entity_type = %s AND action = %s" in sql
        assert "ORDER BY timestamp DESC" in sql
        assert params == ("mcp_server", "started", 25)

    async def test_failure_is_wrapped_as_persistence_error(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")
        with pytest.raises(PersistenceError, match="Failed to get recent actions"):
            await repo.get_recent_actions("mcp_server", AuditAction.STARTED)


class TestGetByCaller:
    async def test_without_action_filters_only_by_caller(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []
        await repo.get_by_caller("user-1")
        sql, params = mock_cursor.execute.call_args[0]
        assert "WHERE caller_user_id = %s" in sql
        assert "AND action" not in sql
        assert params == ["user-1", 100, 0]

    async def test_with_action_filter(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []
        await repo.get_by_caller("user-1", action=AuditAction.DELETED, limit=20, offset=10)
        sql, params = mock_cursor.execute.call_args[0]
        assert "AND action = %s" in sql
        assert params == ["user-1", "deleted", 20, 10]

    async def test_failure_is_wrapped_as_persistence_error(self):
        repo, _, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")
        with pytest.raises(PersistenceError, match="Failed to get audit entries by caller"):
            await repo.get_by_caller("user-1")
