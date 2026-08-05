"""Tests for PostgresMcpServerConfigRepository.

Runs without a live PostgreSQL: `IConnectionFactory.get_connection()` is
faked with a small class (mirroring `tests/unit/test_auth_coverage_batch4.py`)
that yields a MagicMock connection whose `cursor()` yields a MagicMock cursor.
Assertions check the SQL that was executed (placeholders, table name) and the
Python-side behaviour -- optimistic locking, soft vs hard delete, JSON
round-tripping -- never a real database.
"""

from contextlib import contextmanager
import json
from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.domain.contracts.persistence import (
    ConcurrentModificationError,
    McpServerConfigSnapshot,
    PersistenceError,
)
from mcp_hangar.infrastructure.persistence.backends.postgresql.config_repository import (
    PostgresMcpServerConfigRepository,
)


def _make_repo():
    """Build a repository against a fully mocked `IConnectionFactory`.

    The port, not a bare callable: the repository depends on
    `IConnectionFactory`, so the double has to be one too.
    """
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)

    class _Factory:
        @contextmanager
        def get_connection(self):
            yield mock_conn

    connection_factory = _Factory()
    repo = PostgresMcpServerConfigRepository(connection_factory=connection_factory)

    # __init__ already issued the schema-creation execute() -- clear the mocks
    # so each test's assertions reflect only its own calls.
    mock_cursor.reset_mock()
    mock_conn.reset_mock()

    return repo, mock_conn, mock_cursor


def _sample_config(mcp_server_id: str = "srv-1", **overrides) -> McpServerConfigSnapshot:
    fields = {
        "mcp_server_id": mcp_server_id,
        "mode": "container",
        "image": "example/image:latest",
        "enabled": True,
    }
    fields.update(overrides)
    return McpServerConfigSnapshot(**fields)


class TestInit:
    def test_creates_schema_and_commits(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)

        class _Factory:
            @contextmanager
            def get_connection(self):
                yield mock_conn

        PostgresMcpServerConfigRepository(connection_factory=_Factory())

        sql = mock_cursor.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS mcp_server_configs" in sql
        assert "CREATE INDEX IF NOT EXISTS idx_mcp_server_configs_enabled" in sql
        mock_conn.commit.assert_called_once()

    def test_schema_creation_failure_rolls_back_pooled_connection(self):
        """A pooled connection returned without a rollback stays in an
        aborted transaction for whichever caller borrows it next -- even
        during construction."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)
        mock_cursor.execute.side_effect = RuntimeError("connection reset")

        class _Factory:
            @contextmanager
            def get_connection(self):
                yield mock_conn

        with pytest.raises(RuntimeError):
            PostgresMcpServerConfigRepository(connection_factory=_Factory())

        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()


class TestSave:
    async def test_insert_new_config_uses_postgres_placeholders(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = None  # no existing row

        config = _sample_config()
        await repo.save(config)

        # First call: SELECT version check. Second call: INSERT.
        assert mock_cursor.execute.call_count == 2
        select_sql = mock_cursor.execute.call_args_list[0][0][0]
        insert_sql, insert_params = mock_cursor.execute.call_args_list[1][0]

        assert "%s" in select_sql
        assert "?" not in select_sql
        assert "INSERT INTO mcp_server_configs" in insert_sql
        assert "%s" in insert_sql
        assert "?" not in insert_sql
        assert insert_params[0] == "srv-1"
        assert insert_params[3] is True  # enabled passed as a Python bool
        mock_conn.commit.assert_called_once()

    async def test_update_existing_config_increments_version(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = (3,)  # current version
        mock_cursor.rowcount = 1  # UPDATE matched a row

        config = _sample_config()
        await repo.save(config)

        update_sql, update_params = mock_cursor.execute.call_args_list[1][0]
        assert "UPDATE mcp_server_configs" in update_sql
        assert "WHERE mcp_server_id = %s AND version = %s" in update_sql
        # new_version, ..., current_version are both present in the params
        assert 4 in update_params  # new_version = current_version + 1
        assert 3 in update_params  # current_version, used in the WHERE clause
        mock_conn.commit.assert_called_once()

    async def test_version_conflict_raises_and_rolls_back(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = (3,)
        mock_cursor.rowcount = 0  # someone else updated first

        config = _sample_config()
        with pytest.raises(ConcurrentModificationError) as exc_info:
            await repo.save(config)

        assert exc_info.value.mcp_server_id == "srv-1"
        assert exc_info.value.expected_version == 3
        assert exc_info.value.actual_version == 4
        mock_conn.rollback.assert_called_once()
        mock_conn.commit.assert_not_called()

    async def test_unexpected_db_error_wrapped_as_persistence_error(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("connection reset")

        with pytest.raises(PersistenceError, match="Failed to save mcp_server config"):
            await repo.save(_sample_config())

        # A pooled connection returned without a rollback stays in an
        # aborted transaction for whichever concurrent writer borrows it
        # next -- assert the repository actually clears it.
        mock_conn.rollback.assert_called_once()


class TestGet:
    async def test_found_returns_snapshot_from_dict_row(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        stored = _sample_config().to_dict()
        mock_cursor.fetchone.return_value = (stored,)  # psycopg2 already decoded jsonb

        result = await repo.get("srv-1")

        assert result is not None
        assert result.mcp_server_id == "srv-1"
        sql = mock_cursor.execute.call_args[0][0]
        assert "%s" in sql
        assert "WHERE mcp_server_id = %s" in sql

    async def test_found_tolerates_raw_json_string(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        stored = _sample_config().to_dict()
        mock_cursor.fetchone.return_value = (json.dumps(stored),)  # raw text, not decoded

        result = await repo.get("srv-1")

        assert result is not None
        assert result.mcp_server_id == "srv-1"

    async def test_not_found_returns_none(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = None

        result = await repo.get("ghost")
        assert result is None

    async def test_db_error_wrapped_as_persistence_error(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(PersistenceError, match="Failed to get mcp_server config"):
            await repo.get("srv-1")

        mock_conn.rollback.assert_called_once()


class TestGetAll:
    async def test_only_selects_enabled_rows(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []

        await repo.get_all()

        sql = mock_cursor.execute.call_args[0][0]
        assert "WHERE enabled = TRUE" in sql

    async def test_returns_all_deserialized_configs(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        a = _sample_config("srv-a").to_dict()
        b = _sample_config("srv-b").to_dict()
        mock_cursor.fetchall.return_value = [(a,), (b,)]

        results = await repo.get_all()

        assert {c.mcp_server_id for c in results} == {"srv-a", "srv-b"}

    async def test_empty_result_returns_empty_list(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []

        assert await repo.get_all() == []

    async def test_malformed_row_is_skipped_not_raised(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        good = _sample_config("srv-good").to_dict()
        mock_cursor.fetchall.return_value = [("not valid json{{{",), (good,)]

        results = await repo.get_all()

        assert len(results) == 1
        assert results[0].mcp_server_id == "srv-good"

    async def test_db_error_wrapped_as_persistence_error(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(PersistenceError, match="Failed to get all mcp_server configs"):
            await repo.get_all()

        mock_conn.rollback.assert_called_once()


class TestDelete:
    async def test_soft_deletes_by_disabling(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.rowcount = 1

        result = await repo.delete("srv-1")

        assert result is True
        sql, params = mock_cursor.execute.call_args[0]
        assert "SET enabled = FALSE" in sql
        assert "WHERE mcp_server_id = %s AND enabled = TRUE" in sql
        assert params[1] == "srv-1"
        mock_conn.commit.assert_called_once()

    async def test_not_found_returns_false(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        mock_cursor.rowcount = 0

        assert await repo.delete("ghost") is False

    async def test_db_error_wrapped_as_persistence_error(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(PersistenceError, match="Failed to delete mcp_server config"):
            await repo.delete("srv-1")

        mock_conn.rollback.assert_called_once()


class TestHardDelete:
    async def test_deletes_row_permanently(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.rowcount = 1

        result = await repo.hard_delete("srv-1")

        assert result is True
        sql, params = mock_cursor.execute.call_args[0]
        assert sql.strip().startswith("DELETE FROM mcp_server_configs")
        assert params == ("srv-1",)
        mock_conn.commit.assert_called_once()

    async def test_not_found_returns_false(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        mock_cursor.rowcount = 0

        assert await repo.hard_delete("ghost") is False

    async def test_db_error_wrapped_as_persistence_error(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(PersistenceError, match="Failed to hard-delete mcp_server config"):
            await repo.hard_delete("srv-1")

        mock_conn.rollback.assert_called_once()


class TestExists:
    async def test_true_when_enabled_row_present(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = (1,)

        assert await repo.exists("srv-1") is True
        sql = mock_cursor.execute.call_args[0][0]
        assert "WHERE mcp_server_id = %s AND enabled = TRUE" in sql

    async def test_false_when_absent(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = None

        assert await repo.exists("ghost") is False

    async def test_db_error_wrapped_as_persistence_error(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(PersistenceError, match="Failed to check mcp_server existence"):
            await repo.exists("srv-1")

        mock_conn.rollback.assert_called_once()


class TestGetWithVersion:
    async def test_found_returns_tuple(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        stored = _sample_config().to_dict()
        mock_cursor.fetchone.return_value = (stored, 7)

        result = await repo.get_with_version("srv-1")

        assert result is not None
        snapshot, version = result
        assert snapshot.mcp_server_id == "srv-1"
        assert version == 7

    async def test_not_found_returns_none(self):
        repo, _mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = None

        assert await repo.get_with_version("ghost") is None

    async def test_db_error_wrapped_as_persistence_error(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(PersistenceError, match="Failed to get mcp_server config with version"):
            await repo.get_with_version("srv-1")

        mock_conn.rollback.assert_called_once()


class TestUpdateLastStarted:
    async def test_updates_timestamp_and_commits(self):
        repo, mock_conn, mock_cursor = _make_repo()

        await repo.update_last_started("srv-1")

        sql, params = mock_cursor.execute.call_args[0]
        assert "SET last_started_at = %s, updated_at = %s" in sql
        assert params[2] == "srv-1"
        mock_conn.commit.assert_called_once()

    async def test_failure_is_swallowed_not_raised(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")

        # Best-effort: must not raise even though the write failed.
        await repo.update_last_started("srv-1")

        # Swallowing the error must not also swallow the obligation to
        # clear the pooled connection's aborted transaction state.
        mock_conn.rollback.assert_called_once()


class TestUpdateFailureCount:
    async def test_updates_count_and_commits(self):
        repo, mock_conn, mock_cursor = _make_repo()

        await repo.update_failure_count("srv-1", 5)

        sql, params = mock_cursor.execute.call_args[0]
        assert "SET consecutive_failures = %s, updated_at = %s" in sql
        assert params[0] == 5
        assert params[2] == "srv-1"
        mock_conn.commit.assert_called_once()

    async def test_failure_is_swallowed_not_raised(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")

        # Best-effort: must not raise even though the write failed.
        await repo.update_failure_count("srv-1", 2)

        mock_conn.rollback.assert_called_once()


class TestNoDialectBranching:
    """Guards against SQLite fallbacks creeping into a PostgreSQL-only file."""

    def test_source_has_no_sqlite_placeholder_style(self):
        import inspect

        from mcp_hangar.infrastructure.persistence.backends.postgresql import config_repository

        source = inspect.getsource(config_repository)
        # SQLite's positional placeholder. A bare "?" would only show up here
        # if a query string accidentally used SQLite syntax.
        assert "VALUES (?" not in source
        assert "= ?" not in source

    def test_datetime_import_unused_for_timestamp_type_switch(self):
        """created_at/updated_at stay ISO strings, not TIMESTAMPTZ, so both
        backends round-trip the same Python representation."""
        from mcp_hangar.infrastructure.persistence.backends.postgresql.config_repository import _SCHEMA

        assert "created_at TEXT NOT NULL" in _SCHEMA
        assert "updated_at TEXT NOT NULL" in _SCHEMA
        assert "TIMESTAMPTZ" not in _SCHEMA


class TestEnabledFlagIsBoolean:
    async def test_delete_sets_enabled_false_not_zero(self):
        """The reference uses SQLite's INTEGER 0/1; Postgres has a real BOOLEAN."""
        repo, _mock_conn, mock_cursor = _make_repo()
        mock_cursor.rowcount = 1

        await repo.delete("srv-1")

        sql = mock_cursor.execute.call_args[0][0]
        assert "enabled = FALSE" in sql
        assert "enabled = 0" not in sql
