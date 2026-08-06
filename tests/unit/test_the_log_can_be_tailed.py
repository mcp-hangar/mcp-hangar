"""Following the log is a store capability, and stores differ on how.

A position works as a resume point only where appends are serialized. On
PostgreSQL it silently loses events, which is why `read_since` takes an opaque
cursor rather than a number: a caller holding an integer has already assumed one
store's answer.

These tests cover the contract and the refusal. That the refusal is *needed* --
that a position cursor really does skip on PostgreSQL -- is not something a fake
can show, so it is asserted against a real server in
`tests/integration/test_postgres_tail_does_not_skip.py`.
"""

from __future__ import annotations

import pytest

from mcp_hangar.domain.contracts.event_store import (
    BEGINNING,
    IEventStore,
    NullEventStore,
    TailCursor,
    TailingNotSupportedError,
)
from mcp_hangar.domain.events import McpServerStarted
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore


def _event(server_id: str = "math") -> McpServerStarted:
    return McpServerStarted(mcp_server_id=server_id, mode="subprocess", tools_count=1, startup_duration_ms=1.0)


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path):
    """The two stores whose appends are serialized, tested identically."""
    if request.param == "sqlite":
        return SQLiteEventStore(str(tmp_path / "events.db"))
    return InMemoryEventStore()


class TestFollowingTheLog:
    def test_a_tail_from_the_beginning_sees_everything(self, store) -> None:
        store.append("mcp_server:a", [_event("a")], expected_version=-1)
        store.append("mcp_server:b", [_event("b")], expected_version=-1)

        batch, _cursor = store.read_since(BEGINNING)

        assert [stream for stream, _ in batch] == ["mcp_server:a", "mcp_server:b"]

    def test_what_was_read_once_is_not_read_again(self, store) -> None:
        # The property a tailer runs on: it polls in a loop, and a handler that
        # ran on an event must not run on it a second time just because the
        # loop went round.
        store.append("mcp_server:a", [_event("a")], expected_version=-1)
        _batch, cursor = store.read_since(BEGINNING)

        batch, _cursor = store.read_since(cursor)

        assert batch == []

    def test_what_arrives_after_the_cursor_is_picked_up(self, store) -> None:
        store.append("mcp_server:a", [_event("a")], expected_version=-1)
        _batch, cursor = store.read_since(BEGINNING)

        store.append("mcp_server:b", [_event("b")], expected_version=-1)
        batch, _cursor = store.read_since(cursor)

        assert [stream for stream, _ in batch] == ["mcp_server:b"]

    def test_a_batch_is_bounded_and_resumes_where_it_stopped(self, store) -> None:
        # A replica that has been down for a while must not pull the entire log
        # into memory in one read, and must not lose the remainder either.
        for index in range(5):
            store.append(f"mcp_server:{index}", [_event(str(index))], expected_version=-1)

        first, cursor = store.read_since(BEGINNING, limit=2)
        second, cursor = store.read_since(cursor, limit=2)
        third, _cursor = store.read_since(cursor, limit=2)

        assert [len(first), len(second), len(third)] == [2, 2, 1]

    def test_the_head_is_a_cursor_with_nothing_behind_it(self, store) -> None:
        # What a replica takes *before* reading a snapshot, so that anything
        # landing between the two is still delivered rather than falling in the
        # gap between "not in the snapshot yet" and "before my cursor".
        store.append("mcp_server:a", [_event("a")], expected_version=-1)

        head = store.tail_head()

        assert store.read_since(head)[0] == []

    def test_an_event_appended_after_the_head_is_delivered(self, store) -> None:
        store.append("mcp_server:a", [_event("a")], expected_version=-1)
        head = store.tail_head()

        store.append("mcp_server:b", [_event("b")], expected_version=-1)

        assert [stream for stream, _ in store.read_since(head)[0]] == ["mcp_server:b"]


class TestAStoreThatCannotBeTailedSafelySaysSo:
    def test_a_store_that_never_considered_ordering_is_refused(self) -> None:
        # The default is the unsafe one, so silence has to mean refusal. A new
        # backend that inherits without thinking gets an exception naming what
        # to do, rather than a tailer that quietly drops events.
        class _Unconsidered(InMemoryEventStore):
            positions_are_commit_ordered = False

        with pytest.raises(TailingNotSupportedError) as excinfo:
            _Unconsidered().read_since(BEGINNING)

        assert "_Unconsidered" in str(excinfo.value)

    def test_the_head_is_refused_for_the_same_reason(self) -> None:
        class _Unconsidered(InMemoryEventStore):
            positions_are_commit_ordered = False

        with pytest.raises(TailingNotSupportedError):
            _Unconsidered().tail_head()

    def test_the_unsafe_answer_is_what_a_store_gets_by_not_deciding(self) -> None:
        # Stated as a test because it is the design: the flag defaults to False
        # on the port, and each store that qualifies says so for a reason
        # written next to it.
        assert IEventStore.positions_are_commit_ordered is False
        assert SQLiteEventStore.positions_are_commit_ordered is True
        assert InMemoryEventStore.positions_are_commit_ordered is True

    def test_postgres_does_not_rely_on_the_flag_at_all(self) -> None:
        # It cannot claim commit-ordered positions, so it implements its own
        # resume token instead. If this ever inverted, the flag would be the
        # thing that made a broken tailer look supported.
        from mcp_hangar.infrastructure.persistence.backends.postgresql.event_store import PostgresEventStore

        assert PostgresEventStore.positions_are_commit_ordered is False
        assert PostgresEventStore.read_since is not IEventStore.read_since
        assert PostgresEventStore.tail_head is not IEventStore.tail_head


class TestTheDiscardingStore:
    def test_tailing_it_yields_nothing_rather_than_failing(self) -> None:
        # Event persistence can be switched off. A tailer must then find an
        # empty log, not an exception -- `can_replay` is how a caller learns
        # that the silence means "not kept".
        store = NullEventStore()

        assert store.read_since(BEGINNING)[0] == []
        assert store.can_replay is False


class TestTheCursorIsOpaque:
    def test_it_does_not_invite_arithmetic(self) -> None:
        # A cursor is not a number on every store, so the type must not let a
        # caller treat it as one -- that is how a position assumption gets
        # written into shared code.
        cursor = TailCursor("742:0")

        assert not hasattr(cursor, "__add__")
        with pytest.raises(Exception):
            cursor.token = "1"  # type: ignore[misc]
