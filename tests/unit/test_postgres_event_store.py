"""Tests for PostgresEventStore.

No live PostgreSQL is used. Two complementary styles:

- ``TestSqlShapes`` mocks the connection factory with a bare ``MagicMock``
  cursor and asserts on the literal SQL text issued (table names, ``%s``
  placeholders, ``ON CONFLICT`` clauses) -- the same style as
  ``test_auth_coverage_batch4.py``.
- ``_FakePostgres`` (used by the rest of the classes) is a tiny in-memory
  stand-in that actually interprets the small, fixed set of statements this
  adapter issues, so the higher-level tests can assert on real round-trip
  behaviour (append -> read, concurrency conflicts, snapshots, compaction)
  without touching a database.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from mcp_hangar.domain.contracts.event_store import ConcurrencyError
from mcp_hangar.domain.events import McpServerStarted, McpServerStopped
from mcp_hangar.domain.exceptions import CompactionError
from mcp_hangar.infrastructure.persistence.backends.postgresql.event_store import PostgresEventStore


def _norm(sql: str) -> str:
    """Collapse whitespace for reliable substring/prefix comparisons."""
    return " ".join(sql.split())


def _make_event(server_id: str = "math") -> McpServerStarted:
    return McpServerStarted(
        mcp_server_id=server_id,
        mode="subprocess",
        tools_count=3,
        startup_duration_ms=50.0,
    )


# ============================================================================
# SQL-shape assertions against a bare MagicMock
# ============================================================================


class _NullFactory:
    """A factory whose connection is never touched -- for constructor-only tests."""

    @contextmanager
    def get_connection(self) -> Iterator[Any]:
        yield None


class TestConstruction:
    def test_default_table_names(self):
        store = PostgresEventStore(_NullFactory())
        assert store._events_table == "events"
        assert store._streams_table == "streams"
        assert store._snapshots_table == "snapshots"

    def test_table_prefix(self):
        store = PostgresEventStore(_NullFactory(), table_prefix="es_")
        assert store._events_table == "es_events"
        assert store._streams_table == "es_streams"
        assert store._snapshots_table == "es_snapshots"


class TestSqlShapes:
    """Assert on the literal SQL text, the way test_auth_coverage_batch4.py does."""

    def _mock_store(self) -> tuple[PostgresEventStore, MagicMock, MagicMock]:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        class _Factory:
            @contextmanager
            def get_connection(self):
                yield mock_conn

        store = PostgresEventStore(_Factory())
        return store, mock_conn, mock_cursor

    def test_initialize_uses_create_table_if_not_exists(self):
        store, mock_conn, mock_cursor = self._mock_store()
        store.initialize()

        sql = _norm(mock_cursor.execute.call_args[0][0])
        assert "CREATE TABLE IF NOT EXISTS events" in sql
        assert "CREATE TABLE IF NOT EXISTS streams" in sql
        assert "CREATE TABLE IF NOT EXISTS snapshots" in sql
        assert "BIGSERIAL PRIMARY KEY" in sql
        assert "UNIQUE(stream_id, stream_version)" in sql
        mock_conn.commit.assert_called_once()

    def test_append_new_stream_uses_insert_on_conflict_do_nothing(self):
        store, mock_conn, mock_cursor = self._mock_store()
        mock_cursor.fetchone.return_value = ("s1",)

        store.append("s1", [_make_event()], expected_version=-1)

        first_sql, first_params = mock_cursor.execute.call_args_list[0][0]
        first_sql = _norm(first_sql)
        assert first_sql.startswith("INSERT INTO streams")
        assert "ON CONFLICT (stream_id) DO NOTHING" in first_sql
        assert "RETURNING stream_id" in first_sql
        assert "%s" in first_sql and "?" not in first_sql
        # New stream: version is written directly as expected_version + len(events).
        assert first_params == ("s1", 0, first_params[2], first_params[3])

    def test_append_existing_stream_uses_conditional_update(self):
        store, mock_conn, mock_cursor = self._mock_store()
        mock_cursor.fetchone.return_value = ("s1",)

        store.append("s1", [_make_event()], expected_version=4)

        first_sql, first_params = mock_cursor.execute.call_args_list[0][0]
        first_sql = _norm(first_sql)
        assert first_sql.startswith("UPDATE streams")
        assert "SET version = %s" in first_sql
        assert "WHERE stream_id = %s AND version = %s" in first_sql
        assert "RETURNING stream_id" in first_sql
        # new_version, updated_at, stream_id, expected_version
        assert first_params[0] == 5
        assert first_params[2] == "s1"
        assert first_params[3] == 4

    def test_append_inserts_event_rows_with_percent_s_placeholders(self):
        store, mock_conn, mock_cursor = self._mock_store()
        mock_cursor.fetchone.return_value = ("s1",)

        store.append("s1", [_make_event()], expected_version=-1)

        event_sql, event_params = mock_cursor.execute.call_args_list[1][0]
        event_sql = _norm(event_sql)
        assert event_sql.startswith("INSERT INTO events")
        assert "(stream_id, stream_version, event_type, data, created_at)" in event_sql
        assert event_sql.count("%s") == 5
        assert event_params[0] == "s1"
        assert event_params[1] == 0  # -1 + 1

    def test_save_snapshot_uses_upsert(self):
        store, mock_conn, mock_cursor = self._mock_store()
        store.save_snapshot("s1", 3, {"foo": "bar"})

        sql = _norm(mock_cursor.execute.call_args[0][0])
        assert sql.startswith("INSERT INTO snapshots")
        assert "ON CONFLICT (stream_id) DO UPDATE SET" in sql
        assert "version = EXCLUDED.version" in sql

    def test_compact_stream_without_snapshot_never_touches_connection(self):
        store, mock_conn, mock_cursor = self._mock_store()
        mock_cursor.fetchone.return_value = None  # load_snapshot finds nothing

        with pytest.raises(CompactionError):
            store.compact_stream("s1")

        # Only the snapshot lookup should have run -- no DELETE issued.
        for call in mock_cursor.execute.call_args_list:
            assert "DELETE" not in _norm(call[0][0])

    def test_append_empty_events_never_touches_connection(self):
        store, mock_conn, mock_cursor = self._mock_store()

        result = store.append("s1", [], expected_version=7)

        assert result == 7
        mock_cursor.execute.assert_not_called()
        mock_conn.commit.assert_not_called()

    def test_as_json_text_passes_through_strings_and_dumps_objects(self):
        assert PostgresEventStore._as_json_text("{}") == "{}"
        assert PostgresEventStore._as_json_text({"a": 1}) == '{"a": 1}'


# ============================================================================
# Behavioural round-trip tests against a tiny in-memory fake
# ============================================================================


class _FakeCursor:
    """Interprets exactly the fixed set of statements PostgresEventStore issues."""

    def __init__(self, db: "_FakePostgres"):
        self._db = db
        self._result: list[tuple] = []
        self.rowcount = 0

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        s = _norm(sql)
        self._db.executed.append(s)
        self._result = []
        self.rowcount = 0

        for prefix, handler in self._HANDLERS:
            if s.startswith(prefix):
                handler(self, params)
                return

        raise AssertionError(f"_FakeCursor got an unrecognised statement: {s!r}")

    def _noop(self, params: tuple) -> None:
        pass

    def _reserve_new_stream(self, params: tuple) -> None:
        stream_id, version, created_at, updated_at = params
        if stream_id in self._db.streams:
            return
        self._db.streams[stream_id] = {"version": version, "created_at": created_at, "updated_at": updated_at}
        self._result = [(stream_id,)]
        self.rowcount = 1

    def _advance_existing_stream(self, params: tuple) -> None:
        new_version, updated_at, stream_id, expected_version = params
        row = self._db.streams.get(stream_id)
        if row is None or row["version"] != expected_version:
            return
        row["version"] = new_version
        row["updated_at"] = updated_at
        self._result = [(stream_id,)]
        self.rowcount = 1

    def _select_stream_version(self, params: tuple) -> None:
        (stream_id,) = params
        row = self._db.streams.get(stream_id)
        self._result = [(row["version"],)] if row else []

    def _insert_event(self, params: tuple) -> None:
        stream_id, stream_version, event_type, data, created_at = params
        self._db.events.append(
            {
                "global_position": self._db.next_position(),
                "stream_id": stream_id,
                "stream_version": stream_version,
                "event_type": event_type,
                "data": data,
                "created_at": created_at,
            }
        )
        self.rowcount = 1

    def _select_stream_events(self, params: tuple) -> None:
        stream_id, from_version = params
        rows = sorted(
            (e for e in self._db.events if e["stream_id"] == stream_id and e["stream_version"] >= from_version),
            key=lambda e: e["stream_version"],
        )
        self._result = [(e["event_type"], e["data"]) for e in rows]

    def _select_all_events(self, params: tuple) -> None:
        from_position, limit = params
        rows = sorted(
            (e for e in self._db.events if e["global_position"] > from_position),
            key=lambda e: e["global_position"],
        )[:limit]
        self._result = [(e["global_position"], e["stream_id"], e["event_type"], e["data"]) for e in rows]

    def _select_streams_like(self, params: tuple) -> None:
        (pattern,) = params
        prefix = pattern[:-1]
        self._result = [(sid,) for sid in sorted(self._db.streams) if sid.startswith(prefix)]

    def _select_all_stream_ids(self, params: tuple) -> None:
        self._result = [(sid,) for sid in sorted(self._db.streams)]

    def _count_events(self, params: tuple) -> None:
        self._result = [(len(self._db.events),)]

    def _count_streams(self, params: tuple) -> None:
        self._result = [(len(self._db.streams),)]

    def _upsert_snapshot(self, params: tuple) -> None:
        stream_id, version, state_data, created_at = params
        self._db.snapshots[stream_id] = {"version": version, "state_data": state_data, "created_at": created_at}
        self.rowcount = 1

    def _select_snapshot(self, params: tuple) -> None:
        (stream_id,) = params
        row = self._db.snapshots.get(stream_id)
        self._result = [(row["version"], row["state_data"])] if row else []

    def _delete_events_up_to(self, params: tuple) -> None:
        stream_id, max_version = params
        before = len(self._db.events)
        self._db.events = [
            e for e in self._db.events if not (e["stream_id"] == stream_id and e["stream_version"] <= max_version)
        ]
        self.rowcount = before - len(self._db.events)

    # Order matters: more specific prefixes (e.g. the LIKE variant of a
    # SELECT) must be checked before their shorter, more general prefixes.
    _HANDLERS = [
        ("CREATE TABLE", _noop),
        ("INSERT INTO streams", _reserve_new_stream),
        ("UPDATE streams", _advance_existing_stream),
        ("SELECT version FROM streams", _select_stream_version),
        ("INSERT INTO events", _insert_event),
        ("SELECT event_type, data FROM events", _select_stream_events),
        ("SELECT global_position, stream_id, event_type, data FROM events", _select_all_events),
        ("SELECT stream_id FROM streams WHERE stream_id LIKE", _select_streams_like),
        ("SELECT stream_id FROM streams ORDER BY stream_id", _select_all_stream_ids),
        ("SELECT COUNT(*) FROM events", _count_events),
        ("SELECT COUNT(*) FROM streams", _count_streams),
        ("INSERT INTO snapshots", _upsert_snapshot),
        ("SELECT version, state_data FROM snapshots", _select_snapshot),
        ("DELETE FROM events", _delete_events_up_to),
    ]

    def fetchone(self) -> tuple | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[tuple]:
        return list(self._result)


class _FakeConnection:
    def __init__(self, db: "_FakePostgres"):
        self._db = db
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._db)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _FakePostgres:
    """Holds the state a real Postgres backend would hold across connections."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.streams: dict[str, dict] = {}
        self.snapshots: dict[str, dict] = {}
        self.executed: list[str] = []
        self._position = 0
        # The connection most recently handed out by get_connection(), so
        # tests can inspect whether *that* call committed/rolled back --
        # a real pooled connection is reused by the next borrower, so an
        # uncommitted read leaves it "idle in transaction" for them.
        self.last_conn: _FakeConnection | None = None

    def next_position(self) -> int:
        self._position += 1
        return self._position

    @contextmanager
    def get_connection(self) -> Iterator[_FakeConnection]:
        conn = _FakeConnection(self)
        self.last_conn = conn
        yield conn


@pytest.fixture
def db() -> _FakePostgres:
    return _FakePostgres()


@pytest.fixture
def store(db: _FakePostgres) -> PostgresEventStore:
    s = PostgresEventStore(db)
    s.initialize()
    return s


class TestAppendAndReadStream:
    def test_append_to_new_stream_returns_version(self, store: PostgresEventStore):
        version = store.append("provider:math", [_make_event()], expected_version=-1)
        assert version == 0

    def test_append_multiple_events_increments_version_per_event(self, store: PostgresEventStore):
        events = [_make_event(), McpServerStopped(mcp_server_id="math", reason="restart")]
        version = store.append("provider:math", events, expected_version=-1)
        assert version == 1

    def test_read_stream_round_trips_events_in_order(self, store: PostgresEventStore):
        store.append("provider:math", [_make_event()], expected_version=-1)
        store.append("provider:math", [McpServerStopped(mcp_server_id="math", reason="restart")], expected_version=0)

        events = store.read_stream("provider:math")

        assert len(events) == 2
        assert isinstance(events[0], McpServerStarted)
        assert isinstance(events[1], McpServerStopped)
        assert events[1].reason == "restart"

    def test_read_stream_from_version_filters(self, store: PostgresEventStore):
        store.append("provider:math", [_make_event()], expected_version=-1)
        store.append("provider:math", [McpServerStopped(mcp_server_id="math", reason="restart")], expected_version=0)

        events = store.read_stream("provider:math", from_version=1)

        assert len(events) == 1
        assert isinstance(events[0], McpServerStopped)

    def test_read_stream_missing_stream_returns_empty(self, store: PostgresEventStore):
        assert store.read_stream("provider:missing") == []

    def test_append_empty_events_is_a_noop(self, store: PostgresEventStore, db: _FakePostgres):
        version = store.append("provider:math", [], expected_version=3)
        assert version == 3
        assert db.events == []
        assert db.streams == {}


class TestConcurrency:
    def test_conflicting_expected_version_on_new_stream_raises(self, store: PostgresEventStore):
        store.append("provider:math", [_make_event()], expected_version=-1)

        with pytest.raises(ConcurrencyError) as exc_info:
            store.append("provider:math", [_make_event()], expected_version=-1)

        assert exc_info.value.stream_id == "provider:math"
        assert exc_info.value.expected == -1
        assert exc_info.value.actual == 0

    def test_stale_expected_version_raises_with_actual_version(self, store: PostgresEventStore):
        store.append("provider:math", [_make_event()], expected_version=-1)
        store.append("provider:math", [_make_event()], expected_version=0)

        with pytest.raises(ConcurrencyError) as exc_info:
            store.append("provider:math", [_make_event()], expected_version=0)

        assert exc_info.value.expected == 0
        assert exc_info.value.actual == 1

    def test_expected_version_against_nonexistent_stream_reports_actual_minus_one(self, store: PostgresEventStore):
        with pytest.raises(ConcurrencyError) as exc_info:
            store.append("provider:ghost", [_make_event()], expected_version=2)

        assert exc_info.value.actual == -1

    def test_conflict_leaves_no_event_rows_and_rolls_back(self, store: PostgresEventStore, db: _FakePostgres):
        store.append("provider:math", [_make_event()], expected_version=-1)

        with pytest.raises(ConcurrencyError):
            store.append("provider:math", [_make_event(), _make_event()], expected_version=5)

        # Only the first, successful append's event should exist.
        assert len(db.events) == 1
        assert store.get_stream_version("provider:math") == 0


class TestStreamQueries:
    def test_get_stream_version_unknown_stream_is_minus_one(self, store: PostgresEventStore):
        assert store.get_stream_version("nope") == -1

    def test_get_stream_version_after_append(self, store: PostgresEventStore):
        store.append("s1", [_make_event(), _make_event()], expected_version=-1)
        assert store.get_stream_version("s1") == 1

    def test_get_all_stream_ids(self, store: PostgresEventStore):
        store.append("b", [_make_event()], expected_version=-1)
        store.append("a", [_make_event()], expected_version=-1)
        assert store.get_all_stream_ids() == ["a", "b"]

    def test_list_streams_no_prefix_returns_all(self, store: PostgresEventStore):
        store.append("provider:math", [_make_event()], expected_version=-1)
        store.append("provider:calc", [_make_event()], expected_version=-1)
        assert store.list_streams() == ["provider:calc", "provider:math"]

    def test_list_streams_with_prefix_filters(self, store: PostgresEventStore):
        store.append("provider:math", [_make_event()], expected_version=-1)
        store.append("group:default", [_make_event()], expected_version=-1)
        assert store.list_streams(prefix="provider:") == ["provider:math"]

    def test_get_event_count(self, store: PostgresEventStore):
        store.append("s1", [_make_event(), _make_event()], expected_version=-1)
        store.append("s2", [_make_event()], expected_version=-1)
        assert store.get_event_count() == 3

    def test_get_stream_count(self, store: PostgresEventStore):
        store.append("s1", [_make_event()], expected_version=-1)
        store.append("s2", [_make_event()], expected_version=-1)
        assert store.get_stream_count() == 2


class TestReadAll:
    def test_read_all_orders_by_global_position_across_streams(self, store: PostgresEventStore):
        store.append("s1", [_make_event("a")], expected_version=-1)
        store.append("s2", [_make_event("b")], expected_version=-1)
        store.append("s1", [_make_event("a")], expected_version=0)

        results = list(store.read_all())

        positions = [r[0] for r in results]
        stream_ids = [r[1] for r in results]
        assert positions == sorted(positions)
        assert stream_ids == ["s1", "s2", "s1"]

    def test_read_all_from_position_is_exclusive(self, store: PostgresEventStore):
        store.append("s1", [_make_event()], expected_version=-1)
        store.append("s1", [_make_event()], expected_version=0)

        first_batch = list(store.read_all(limit=1))
        first_position = first_batch[0][0]

        remaining = list(store.read_all(from_position=first_position))

        assert len(remaining) == 1
        assert remaining[0][0] > first_position

    def test_read_all_respects_limit(self, store: PostgresEventStore):
        store.append("s1", [_make_event(), _make_event(), _make_event()], expected_version=-1)

        assert len(list(store.read_all(limit=2))) == 2

    def test_read_all_yields_deserialized_events(self, store: PostgresEventStore):
        store.append("s1", [_make_event()], expected_version=-1)

        _, _, event = next(iter(store.read_all()))

        assert isinstance(event, McpServerStarted)


class TestSnapshotsAndCompaction:
    def test_load_snapshot_missing_returns_none(self, store: PostgresEventStore):
        assert store.load_snapshot("nope") is None

    def test_save_and_load_snapshot_round_trips(self, store: PostgresEventStore):
        store.save_snapshot("s1", 4, {"tools": ["a", "b"], "count": 2})

        snapshot = store.load_snapshot("s1")

        assert snapshot == {"version": 4, "state": {"tools": ["a", "b"], "count": 2}}

    def test_save_snapshot_overwrites_previous(self, store: PostgresEventStore):
        store.save_snapshot("s1", 1, {"v": 1})
        store.save_snapshot("s1", 2, {"v": 2})

        assert store.load_snapshot("s1") == {"version": 2, "state": {"v": 2}}

    def test_compact_stream_without_snapshot_raises(self, store: PostgresEventStore):
        with pytest.raises(CompactionError):
            store.compact_stream("s1")

    def test_compact_stream_deletes_events_up_to_snapshot_version(self, store: PostgresEventStore):
        events = [_make_event(), _make_event(), _make_event()]
        store.append("s1", events, expected_version=-1)  # versions 0, 1, 2
        store.save_snapshot("s1", 1, {"state": "at-v1"})

        deleted = store.compact_stream("s1")

        assert deleted == 2
        remaining = store.read_stream("s1")
        assert len(remaining) == 1

    def test_compact_stream_returns_zero_when_nothing_precedes_snapshot(self, store: PostgresEventStore):
        store.append("s1", [_make_event()], expected_version=-1)  # version 0
        store.save_snapshot("s1", -1, {"state": "empty"})

        assert store.compact_stream("s1") == 0
        assert len(store.read_stream("s1")) == 1


class TestReadOnlyConnectionHygiene:
    """A SELECT still opens a transaction on the connection it runs on.

    Every read-only method must commit before releasing the connection --
    otherwise a pooled connection goes back "idle in transaction" for
    whoever borrows it next (see PostgresMetricsHistoryStore.query, which
    documents and follows the same rule).
    """

    def test_read_stream_commits(self, store: PostgresEventStore, db: _FakePostgres):
        store.append("s1", [_make_event()], expected_version=-1)

        store.read_stream("s1")

        assert db.last_conn is not None
        assert db.last_conn.commits == 1
        assert db.last_conn.rollbacks == 0

    def test_read_all_commits(self, store: PostgresEventStore, db: _FakePostgres):
        store.append("s1", [_make_event()], expected_version=-1)

        list(store.read_all())

        assert db.last_conn is not None
        assert db.last_conn.commits == 1

    def test_get_stream_version_commits(self, store: PostgresEventStore, db: _FakePostgres):
        store.get_stream_version("s1")

        assert db.last_conn is not None
        assert db.last_conn.commits == 1

    def test_get_all_stream_ids_commits(self, store: PostgresEventStore, db: _FakePostgres):
        store.get_all_stream_ids()

        assert db.last_conn is not None
        assert db.last_conn.commits == 1

    def test_get_event_count_commits(self, store: PostgresEventStore, db: _FakePostgres):
        store.get_event_count()

        assert db.last_conn is not None
        assert db.last_conn.commits == 1

    def test_get_stream_count_commits(self, store: PostgresEventStore, db: _FakePostgres):
        store.get_stream_count()

        assert db.last_conn is not None
        assert db.last_conn.commits == 1

    def test_list_streams_commits(self, store: PostgresEventStore, db: _FakePostgres):
        store.list_streams()

        assert db.last_conn is not None
        assert db.last_conn.commits == 1

    def test_load_snapshot_commits(self, store: PostgresEventStore, db: _FakePostgres):
        store.load_snapshot("s1")

        assert db.last_conn is not None
        assert db.last_conn.commits == 1
