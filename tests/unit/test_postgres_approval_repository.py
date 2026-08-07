"""Tests for PostgresApprovalRepository.

Mocks the IConnectionFactory port the way test_auth_coverage_batch4.py does:
a small factory class yielding a MagicMock connection whose cursor() yields a
MagicMock cursor. Assertions are on the SQL text and parameters passed to the
cursor, and on the Python-side ApprovalRequest produced -- never on a real
database.
"""

from contextlib import contextmanager
from datetime import datetime, UTC
import json
from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.infrastructure.persistence.backends.postgresql.approval_repository import (
    PostgresApprovalRepository,
)


def _make_repo():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)

    # The port, not a bare callable: PostgresApprovalRepository depends on
    # `IConnectionFactory`, so the double has to be one too.
    class _Factory:
        @contextmanager
        def get_connection(self):
            yield mock_conn

    repo = PostgresApprovalRepository(connection_factory=_Factory())
    return repo, mock_conn, mock_cursor


def _sample_request(**overrides) -> ApprovalRequest:
    defaults = {
        "approval_id": "appr-1",
        "tool_name": "delete_thing",
        "arguments": {"id": 42},
        "arguments_hash": "hash123",
        "requested_at": datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        "expires_at": datetime(2026, 8, 1, 12, 5, 0, tzinfo=UTC),
        "state": ApprovalState.PENDING,
        "channel": "dashboard",
        "provider_id": "provider-1",
        "correlation_id": "corr-1",
        "requested_by": "user:alice",
        "tenant_id": "tenant-a",
    }
    defaults.update(overrides)
    return ApprovalRequest(**defaults)


def _row_for(request: ApprovalRequest, arguments_json=None) -> tuple:
    """Build a DB row in the exact column order PostgresApprovalRepository selects."""
    return (
        request.approval_id,
        request.provider_id,
        request.tool_name,
        json.dumps(request.arguments) if arguments_json is None else arguments_json,
        request.arguments_hash,
        request.requested_at.isoformat(),
        request.expires_at.isoformat(),
        request.state.value,
        request.channel,
        request.decided_by,
        request.decided_at.isoformat() if request.decided_at else None,
        request.reason,
        request.correlation_id,
        request.requested_by,
        request.tenant_id,
    )


class TestInitAndSchema:
    def test_init_does_not_touch_the_database(self):
        # Constructing must not call get_connection -- table creation is lazy.
        factory = Mock()
        PostgresApprovalRepository(connection_factory=factory)
        factory.get_connection.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_call_creates_table_and_indexes(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = None

        await repo.get("nope")

        executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert any("CREATE TABLE IF NOT EXISTS approval_requests" in sql for sql in executed_sql)
        assert any("idx_approval_state" in sql for sql in executed_sql)
        assert any("idx_approval_expires" in sql for sql in executed_sql)
        assert any("ADD COLUMN IF NOT EXISTS requested_by" in sql for sql in executed_sql)
        assert any("ADD COLUMN IF NOT EXISTS tenant_id" in sql for sql in executed_sql)

    @pytest.mark.asyncio
    async def test_table_created_only_once_across_calls(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = None

        await repo.get("a")
        first_call_count = mock_cursor.execute.call_count
        await repo.get("b")
        second_call_count = mock_cursor.execute.call_count

        # Second call issues exactly one more SELECT, no more schema statements.
        assert second_call_count == first_call_count + 1

    @pytest.mark.asyncio
    async def test_schema_setup_takes_a_transaction_scoped_advisory_lock(self):
        """`CREATE TABLE IF NOT EXISTS` is not race-free across concurrent
        processes in Postgres; schema setup must serialize via an advisory
        lock rather than relying on `IF NOT EXISTS` alone."""
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = None

        await repo.get("nope")

        executed_sql = [call.args[0] for call in mock_cursor.execute.call_args_list]
        lock_calls = [sql for sql in executed_sql if "pg_advisory_xact_lock" in sql]
        assert len(lock_calls) == 1, "one acquisition covers every statement in the transaction"
        # The lock rides in the same statement as the first DDL, ahead of it --
        # it used to be a separate execute under a key private to this adapter,
        # which left the other nine tables in this backend unserialized.
        locked = lock_calls[0]
        assert locked.index("pg_advisory_xact_lock") < locked.index("CREATE TABLE IF NOT EXISTS approval_requests")
        # And it is the backend's key, not one of this table's own.
        from mcp_hangar.infrastructure.persistence.database_common import POSTGRES_SCHEMA_LOCK_KEY

        assert str(POSTGRES_SCHEMA_LOCK_KEY) in locked

    @pytest.mark.asyncio
    async def test_schema_setup_rolls_back_on_failure(self):
        """If schema DDL fails partway through, the connection must not be
        handed back to the pool sitting inside an aborted transaction."""
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.execute.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await repo.get("nope")

        mock_conn.rollback.assert_called()
        mock_conn.commit.assert_not_called()


class TestSave:
    @pytest.mark.asyncio
    async def test_save_inserts_with_percent_s_placeholders(self):
        repo, mock_conn, mock_cursor = _make_repo()
        request = _sample_request()

        await repo.save(request)

        insert_calls = [c for c in mock_cursor.execute.call_args_list if "INSERT INTO approval_requests" in c.args[0]]
        assert len(insert_calls) == 1
        sql, params = insert_calls[0].args
        assert "?" not in sql
        assert sql.count("%s") == 15
        assert params[0] == "appr-1"
        assert params[1] == "provider-1"
        assert json.loads(params[3]) == {"id": 42}
        assert params[13] == "user:alice"
        assert params[14] == "tenant-a"
        mock_conn.commit.assert_called()

    @pytest.mark.asyncio
    async def test_save_serializes_arguments_as_json(self):
        repo, mock_conn, mock_cursor = _make_repo()
        request = _sample_request(arguments={"nested": {"a": 1}, "list": [1, 2]})

        await repo.save(request)

        sql, params = mock_cursor.execute.call_args_list[-1].args
        assert json.loads(params[3]) == {"nested": {"a": 1}, "list": [1, 2]}

    @pytest.mark.asyncio
    async def test_save_non_json_serializable_argument_uses_str_fallback(self):
        repo, mock_conn, mock_cursor = _make_repo()

        class Weird:
            def __str__(self):
                return "weird-value"

        request = _sample_request(arguments={"obj": Weird()})
        await repo.save(request)

        sql, params = mock_cursor.execute.call_args_list[-1].args
        assert json.loads(params[3]) == {"obj": "weird-value"}

    @pytest.mark.asyncio
    async def test_save_rolls_back_and_reraises_on_failure(self):
        """A failed INSERT (e.g. duplicate approval_id) must not leave the
        pooled connection stuck in an aborted transaction for the next
        borrower -- roll back, then propagate the error rather than
        swallowing it."""
        repo, mock_conn, mock_cursor = _make_repo()
        request = _sample_request()

        def fail_on_insert(sql, *args, **kwargs):
            if "INSERT INTO approval_requests" in sql:
                raise RuntimeError("duplicate key")

        mock_cursor.execute.side_effect = fail_on_insert

        with pytest.raises(RuntimeError, match="duplicate key"):
            await repo.save(request)

        mock_conn.rollback.assert_called()


class TestGet:
    @pytest.mark.asyncio
    async def test_get_not_found_returns_none(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = None

        result = await repo.get("ghost")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_found_returns_approval_request(self):
        repo, mock_conn, mock_cursor = _make_repo()
        request = _sample_request()
        mock_cursor.fetchone.return_value = _row_for(request)

        result = await repo.get("appr-1")

        assert result is not None
        assert result.approval_id == "appr-1"
        assert result.provider_id == "provider-1"
        assert result.tool_name == "delete_thing"
        assert result.arguments == {"id": 42}
        assert result.state == ApprovalState.PENDING
        assert result.requested_by == "user:alice"
        assert result.tenant_id == "tenant-a"

    @pytest.mark.asyncio
    async def test_get_uses_percent_s_placeholder(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchone.return_value = None

        await repo.get("appr-1")

        select_calls = [c for c in mock_cursor.execute.call_args_list if "SELECT" in c.args[0]]
        sql, params = select_calls[-1].args
        assert "%s" in sql
        assert "?" not in sql
        assert params == ("appr-1",)

    @pytest.mark.asyncio
    async def test_row_to_request_accepts_dict_arguments_already_parsed_by_driver(self):
        """psycopg2 auto-decodes JSONB into a dict; the adapter must not re-decode it."""
        repo, mock_conn, mock_cursor = _make_repo()
        request = _sample_request()
        mock_cursor.fetchone.return_value = _row_for(request, arguments_json={"id": 42})

        result = await repo.get("appr-1")
        assert result.arguments == {"id": 42}

    @pytest.mark.asyncio
    async def test_row_to_request_defaults_correlation_id_when_falsy(self):
        repo, mock_conn, mock_cursor = _make_repo()
        request = _sample_request(correlation_id="")
        mock_cursor.fetchone.return_value = _row_for(request)

        result = await repo.get("appr-1")
        assert result.correlation_id == ""

    @pytest.mark.asyncio
    async def test_row_to_request_missing_requested_by_and_tenant_id_defaults_none(self):
        """A short row (legacy pre-migration data) must not crash the parse."""
        repo, mock_conn, mock_cursor = _make_repo()
        request = _sample_request()
        full_row = _row_for(request)
        short_row = full_row[:13]  # drop requested_by / tenant_id
        mock_cursor.fetchone.return_value = short_row

        result = await repo.get("appr-1")
        assert result.requested_by is None
        assert result.tenant_id is None

    @pytest.mark.asyncio
    async def test_row_to_request_decided_at_none_when_not_decided(self):
        repo, mock_conn, mock_cursor = _make_repo()
        request = _sample_request()
        mock_cursor.fetchone.return_value = _row_for(request)

        result = await repo.get("appr-1")
        assert result.decided_at is None

    @pytest.mark.asyncio
    async def test_get_ends_the_read_transaction_before_returning_connection(self):
        """A SELECT still opens a transaction under psycopg2's default
        (non-autocommit) mode. Since this adapter uses a *pooled* connection
        (unlike the SQLite reference, which opens and closes a fresh
        connection per call), failing to end it here would return the
        connection to the pool sitting idle-in-transaction indefinitely."""
        repo, mock_conn, mock_cursor = _make_repo()
        # Prime the schema first so its own commit() doesn't muddy the
        # assertion below -- we only care about the read path's handling.
        mock_cursor.fetchone.return_value = None
        await repo.get("prime")
        mock_conn.commit.reset_mock()
        mock_conn.rollback.reset_mock()

        request = _sample_request()
        mock_cursor.fetchone.return_value = _row_for(request)

        await repo.get("appr-1")

        mock_conn.rollback.assert_called()
        mock_conn.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_row_to_request_decided_at_parsed_when_present(self):
        repo, mock_conn, mock_cursor = _make_repo()
        decided_at = datetime(2026, 8, 1, 12, 3, 0, tzinfo=UTC)
        request = _sample_request(
            state=ApprovalState.APPROVED,
            decided_by="user:bob",
            decided_at=decided_at,
            reason="looks fine",
        )
        mock_cursor.fetchone.return_value = _row_for(request)

        result = await repo.get("appr-1")
        assert result.decided_at == decided_at
        assert result.decided_by == "user:bob"
        assert result.reason == "looks fine"
        assert result.state == ApprovalState.APPROVED


class TestListPendingAndByState:
    @pytest.mark.asyncio
    async def test_list_pending_delegates_to_list_by_state_pending(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []

        await repo.list_pending()

        select_calls = [c for c in mock_cursor.execute.call_args_list if "SELECT" in c.args[0]]
        sql, params = select_calls[-1].args
        assert params[0] == ApprovalState.PENDING.value

    @pytest.mark.asyncio
    async def test_list_by_state_without_provider_id(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []

        await repo.list_by_state(ApprovalState.DENIED)

        select_calls = [c for c in mock_cursor.execute.call_args_list if "SELECT" in c.args[0]]
        sql, params = select_calls[-1].args
        assert "provider_id" not in sql or "AND provider_id" not in sql
        assert params == (ApprovalState.DENIED.value,)
        assert "ORDER BY requested_at DESC" in sql

    @pytest.mark.asyncio
    async def test_list_by_state_with_provider_id_scopes_query(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []

        await repo.list_by_state(ApprovalState.PENDING, provider_id="provider-1")

        select_calls = [c for c in mock_cursor.execute.call_args_list if "SELECT" in c.args[0]]
        sql, params = select_calls[-1].args
        assert "AND provider_id = %s" in sql
        assert params == (ApprovalState.PENDING.value, "provider-1")

    @pytest.mark.asyncio
    async def test_list_by_state_returns_multiple_requests(self):
        repo, mock_conn, mock_cursor = _make_repo()
        r1 = _sample_request(approval_id="a1")
        r2 = _sample_request(approval_id="a2")
        mock_cursor.fetchall.return_value = [_row_for(r1), _row_for(r2)]

        results = await repo.list_by_state(ApprovalState.PENDING)

        assert [r.approval_id for r in results] == ["a1", "a2"]

    @pytest.mark.asyncio
    async def test_list_by_state_empty_result(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []

        results = await repo.list_by_state(ApprovalState.EXPIRED)
        assert results == []

    @pytest.mark.asyncio
    async def test_list_by_state_ends_the_read_transaction_before_returning_connection(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.fetchall.return_value = []
        await repo.list_by_state(ApprovalState.PENDING)  # primes the schema
        mock_conn.commit.reset_mock()
        mock_conn.rollback.reset_mock()

        await repo.list_by_state(ApprovalState.PENDING)

        mock_conn.rollback.assert_called()
        mock_conn.commit.assert_not_called()


class TestUpdateState:
    @pytest.mark.asyncio
    async def test_update_state_uses_percent_s_and_commits(self):
        repo, mock_conn, mock_cursor = _make_repo()
        decided_at = datetime(2026, 8, 1, 12, 4, 0, tzinfo=UTC)

        await repo.update_state(
            "appr-1",
            ApprovalState.APPROVED,
            decided_by="user:bob",
            decided_at=decided_at,
            reason="ok",
        )

        update_calls = [c for c in mock_cursor.execute.call_args_list if "UPDATE approval_requests" in c.args[0]]
        assert len(update_calls) == 1
        sql, params = update_calls[0].args
        assert "?" not in sql
        assert params == (
            ApprovalState.APPROVED.value,
            "user:bob",
            decided_at.isoformat(),
            "ok",
            "appr-1",
        )
        mock_conn.commit.assert_called()

    @pytest.mark.asyncio
    async def test_update_state_none_decided_at_and_reason(self):
        repo, mock_conn, mock_cursor = _make_repo()

        await repo.update_state("appr-1", ApprovalState.EXPIRED, decided_by=None, decided_at=None, reason=None)

        update_calls = [c for c in mock_cursor.execute.call_args_list if "UPDATE approval_requests" in c.args[0]]
        sql, params = update_calls[0].args
        assert params == (ApprovalState.EXPIRED.value, None, None, None, "appr-1")

    @pytest.mark.asyncio
    async def test_update_state_on_missing_row_is_a_silent_no_op(self):
        """Mirrors the SQLite reference: UPDATE with no matching row raises nothing
        and reports nothing -- callers must `get()` to learn whether it landed."""
        repo, mock_conn, mock_cursor = _make_repo()
        # A real driver would report zero rows matched via cursor.rowcount; the
        # adapter must not consult it, since the reference doesn't either.
        mock_cursor.rowcount = 0

        await repo.update_state("ghost", ApprovalState.DENIED, decided_by="admin", decided_at=None, reason=None)

        mock_conn.commit.assert_called()  # no exception, commit still happens

    @pytest.mark.asyncio
    async def test_update_state_rolls_back_and_reraises_on_failure(self):
        repo, mock_conn, mock_cursor = _make_repo()

        def fail_on_update(sql, *args, **kwargs):
            if "UPDATE approval_requests" in sql:
                raise RuntimeError("connection reset")

        mock_cursor.execute.side_effect = fail_on_update

        with pytest.raises(RuntimeError, match="connection reset"):
            await repo.update_state("appr-1", ApprovalState.APPROVED, decided_by=None, decided_at=None, reason=None)

        mock_conn.rollback.assert_called()
