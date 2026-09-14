"""Auth writes to one principal or one key survive a concurrent writer.

Before the fix, `EventSourcedRoleStore._save_assignment` read the stream's
version just before appending and claimed it. The claim guarded nothing,
because the decision to record the event had been made on the state loaded
earlier. A writer landing between the read and the append still turned an
assignment or a revocation into a `ConcurrencyError`, although nothing was
wrong with it.

`EventSourcedApiKeyStore._save_key` claims the version the key was loaded at,
which is a real claim. Before the fix, though, a revocation that lost the race
to a rotation bounced with the same error instead of landing.

The worst case was checked and is pinned here: a write that takes effect in
memory without its event being stored, or a snapshot that brings back a revoked
role. Neither happens. The stores mutate a freshly loaded local aggregate and
read the log back for every decision. The snapshot test below is the evidence
for the second case.

The two `race_*` scenarios take a list of event stores so that
`tests/integration/test_auth_writes_on_one_postgres.py` can run them against
two replicas sharing one PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
import hashlib
from pathlib import Path
import sys
import threading
from typing import Any

import pytest

from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedApiKeyStore, EventSourcedRoleStore
from mcp_hangar.domain.contracts.event_store import ConcurrencyError, IEventStore
from mcp_hangar.domain.events import ApiKeyRevoked, KeyRotated, RoleAssigned, RoleRevoked
from mcp_hangar.domain.exceptions import RevokedCredentialsError
from mcp_hangar.domain.value_objects import Role
from mcp_hangar.infrastructure.persistence import InMemoryEventStore
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore

PRINCIPAL = "principal-1"
ROLE_STREAM = f"{EventSourcedRoleStore.STREAM_PREFIX}:{PRINCIPAL}"
ROLES = ("reader", "writer", "ops-admin", "filler")

WRITERS = 8
OPS_PER_WRITER = 15  # odd, so every writer ends with its role assigned
KEYS = 6


def _in_memory(tmp_path: Path) -> IEventStore:
    return InMemoryEventStore()


def _sqlite_in_memory(tmp_path: Path) -> IEventStore:
    return SQLiteEventStore(":memory:")


def _sqlite_file(tmp_path: Path) -> IEventStore:
    return SQLiteEventStore(tmp_path / "events.db")


EVERY_STORE = pytest.mark.parametrize(
    "make_store",
    [_in_memory, _sqlite_in_memory, _sqlite_file],
    ids=["in-memory", "sqlite-in-memory", "sqlite-file"],
)


@pytest.fixture
def fine_switching() -> Iterator[None]:
    """Switch threads as often as the interpreter allows, so a two-step race interleaves."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


class _LetAnotherWriterIn:
    """An event store that lets another writer in at one chosen moment.

    Forwards everything to a real store. The first time this caller touches
    `stream_id` through one of `methods`, `other_writer` runs first, against
    the real store directly, the way a second request or a second replica
    would. Its write then lands between this caller's load and this caller's
    write, which is the window the race lives in.
    """

    def __init__(
        self,
        inner: IEventStore,
        stream_id: str,
        other_writer: Callable[[], object],
        methods: Sequence[str] = ("append", "append_at_end"),
    ) -> None:
        self._inner = inner
        self._stream_id = stream_id
        self._other_writer = other_writer
        self._methods = tuple(methods)
        self.fired = False

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if name not in self._methods:
            return attribute

        def intercepted(*args: Any, **kwargs: Any) -> Any:
            stream_id = kwargs.get("stream_id", args[0] if args else None)
            if stream_id == self._stream_id and not self.fired:
                self.fired = True
                self._other_writer()
            return attribute(*args, **kwargs)

        return intercepted


class _AlwaysMovedOn:
    """An event store where every write to one stream finds that another writer got there first."""

    def __init__(self, inner: IEventStore, stream_id: str) -> None:
        self._inner = inner
        self._stream_id = stream_id
        self.attempts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def append(self, stream_id: str, events: list, expected_version: int) -> int:
        if stream_id != self._stream_id:
            return self._inner.append(stream_id, events, expected_version)
        self.attempts += 1
        raise ConcurrencyError(stream_id, expected_version, expected_version + 1)


def _role_store(event_store: Any, names: Sequence[str] = ROLES) -> EventSourcedRoleStore:
    store = EventSourcedRoleStore(event_store=event_store)
    for name in names:
        store.add_role(Role(name=name, description="", permissions=frozenset()))
    return store


def _roles(store: EventSourcedRoleStore) -> set[str]:
    return {role.name for role in store.get_roles_for_principal(PRINCIPAL)}


def _replayed_roles(event_store: IEventStore, names: Sequence[str] = ROLES) -> set[str]:
    """What a freshly started process would believe: no snapshot, only the log."""
    return _roles(_role_store(event_store, names))


def _key_store(event_store: Any) -> EventSourcedApiKeyStore:
    return EventSourcedApiKeyStore(event_store=event_store)


def _key_hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _key_stream(raw_key: str) -> str:
    return f"{EventSourcedApiKeyStore.STREAM_PREFIX}:{_key_hash(raw_key)}"


def _one_key(event_store: IEventStore) -> tuple[str, str]:
    """Create one key for the principal; return its raw value and its id."""
    store = _key_store(event_store)
    raw_key = store.create_key(PRINCIPAL, "ci")
    return raw_key, store.list_keys(PRINCIPAL)[0].key_id


def _run(targets: Sequence[Callable[[], None]]) -> None:
    threads = [threading.Thread(target=target) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads), "a writer hung"


class TestARoleWriteIsNotBouncedByAnotherWriter:
    @EVERY_STORE
    def test_a_revocation_lands_although_a_grant_got_in_first(
        self, make_store: Callable[[Path], IEventStore], tmp_path: Path
    ) -> None:
        inner = make_store(tmp_path)
        other = _role_store(inner)
        other.assign_role(PRINCIPAL, "reader")
        store = _role_store(_LetAnotherWriterIn(inner, ROLE_STREAM, lambda: other.assign_role(PRINCIPAL, "writer")))

        store.revoke_role(PRINCIPAL, "reader")

        assert _roles(store) == {"writer"}
        assert _replayed_roles(inner) == {"writer"}

    @EVERY_STORE
    def test_a_grant_lands_although_a_revocation_got_in_first(
        self, make_store: Callable[[Path], IEventStore], tmp_path: Path
    ) -> None:
        inner = make_store(tmp_path)
        other = _role_store(inner)
        other.assign_role(PRINCIPAL, "reader")
        store = _role_store(_LetAnotherWriterIn(inner, ROLE_STREAM, lambda: other.revoke_role(PRINCIPAL, "reader")))

        store.assign_role(PRINCIPAL, "writer")

        assert _roles(store) == {"writer"}
        assert _replayed_roles(inner) == {"writer"}

    @EVERY_STORE
    def test_a_snapshot_taken_across_the_race_does_not_bring_a_revoked_role_back(
        self, make_store: Callable[[Path], IEventStore], tmp_path: Path
    ) -> None:
        """The worst case the advisory asked about, ruled out.

        This caller loads the principal with `ops-admin` assigned. Another
        writer then revokes `ops-admin`, and this caller's grant of `writer` is
        the save that crosses the snapshot threshold. Its aggregate still holds
        `ops-admin`. If the snapshot claimed the version it was saved at, every
        later load in this process would start from a state that still has
        `ops-admin`, and the revocation would be gone until a restart. Instead
        it claims the version it was loaded at, so the revocation is replayed
        on top of it.

        The other writer comes in before this caller reads anything more, so
        the code before the fix, which read the version at save time, takes the
        snapshot too.
        """
        inner = make_store(tmp_path)
        other = _role_store(inner)
        for i in range(EventSourcedRoleStore.SNAPSHOT_INTERVAL - 2):
            (other.assign_role if i % 2 == 0 else other.revoke_role)(PRINCIPAL, "filler")
        other.assign_role(PRINCIPAL, "ops-admin")
        store = _role_store(
            _LetAnotherWriterIn(
                inner,
                ROLE_STREAM,
                lambda: other.revoke_role(PRINCIPAL, "ops-admin"),
                methods=("get_stream_version", "append", "append_at_end"),
            )
        )

        store.assign_role(PRINCIPAL, "writer")

        assert PRINCIPAL in store._snapshot_store, "the save did not snapshot, so this proves nothing"
        assert _roles(store) == {"writer"}
        assert _replayed_roles(inner) == {"writer"}


def race_role_writes(event_stores: Sequence[IEventStore]) -> None:
    """WRITERS threads on one principal, each flipping its own role; replicas take turns.

    Each writer owns one role, so what each call was told is also what replay
    must show: that writer's last call. Snapshots are crossed more than once.
    """
    names = [f"role-{i}" for i in range(WRITERS)]
    stores = [_role_store(event_store, names) for event_store in event_stores]
    failures: list[Exception] = []

    def writer(i: int) -> None:
        store = stores[i % len(stores)]
        for op in range(OPS_PER_WRITER):
            try:
                (store.assign_role if op % 2 == 0 else store.revoke_role)(PRINCIPAL, names[i])
            except Exception as e:  # noqa: BLE001 -- collected and asserted on below
                failures.append(e)

    _run([lambda i=i: writer(i) for i in range(WRITERS)])

    calls = WRITERS * OPS_PER_WRITER
    assert failures == [], f"{len(failures)} of {calls} role writes failed, e.g. {failures[:1]!r}"
    log = event_stores[0].read_stream(ROLE_STREAM)
    for name in names:
        recorded = [e for e in log if isinstance(e, RoleAssigned | RoleRevoked) and e.role_name == name]
        assert len(recorded) == OPS_PER_WRITER, name
    assert _replayed_roles(event_stores[0], names) == set(names)
    for store in stores:
        assert _roles(store) == set(names), "this process's view and the log disagree"


def race_key_writes(event_stores: Sequence[IEventStore]) -> None:
    """For each of KEYS keys of one principal, a revocation and a rotation start together.

    The revocation has to land whichever write gets in first. The rotation
    either lands first or is refused as a rotation of a revoked key. Neither is
    bounced by the other, and a refused rotation stores no new key.
    """
    stores = [_key_store(event_store) for event_store in event_stores]
    raw_keys = [stores[0].create_key(PRINCIPAL, f"key-{i}") for i in range(KEYS)]
    ids_by_name = {meta.name: meta.key_id for meta in stores[0].list_keys(PRINCIPAL)}
    key_ids = [ids_by_name[f"key-{i}"] for i in range(KEYS)]
    start = threading.Barrier(2 * KEYS)
    revoked: dict[int, bool] = {}
    rotated: dict[int, str] = {}
    refused: dict[int, ValueError] = {}
    failures: list[Exception] = []

    def revoke(i: int) -> None:
        start.wait()
        try:
            revoked[i] = stores[i % len(stores)].revoke_key(key_ids[i])
        except Exception as e:  # noqa: BLE001 -- collected and asserted on below
            failures.append(e)

    def rotate(i: int) -> None:
        start.wait()
        try:
            rotated[i] = stores[(i + 1) % len(stores)].rotate_key(key_ids[i])
        except ValueError as e:
            refused[i] = e
        except Exception as e:  # noqa: BLE001 -- collected and asserted on below
            failures.append(e)

    _run([lambda i=i: revoke(i) for i in range(KEYS)] + [lambda i=i: rotate(i) for i in range(KEYS)])

    assert failures == [], f"{len(failures)} key writes failed, e.g. {failures[:1]!r}"
    assert revoked == dict.fromkeys(range(KEYS), True)
    assert set(rotated) | set(refused) == set(range(KEYS))
    assert not set(rotated) & set(refused)

    fresh = _key_store(event_stores[0])
    for i, raw_key in enumerate(raw_keys):
        log = event_stores[0].read_stream(_key_stream(raw_key))
        assert sum(isinstance(e, ApiKeyRevoked) for e in log) == 1, i
        assert sum(isinstance(e, KeyRotated) for e in log) == (1 if i in rotated else 0), i
        for view in [fresh, *stores]:
            with pytest.raises(RevokedCredentialsError):
                view.get_principal_for_key(_key_hash(raw_key))
    for new_raw_key in rotated.values():
        assert fresh.get_principal_for_key(_key_hash(new_raw_key)) is not None
    assert len(fresh.list_keys(PRINCIPAL)) == KEYS + len(rotated)


class _PauseTheFirstScan:
    """An event store that starts another thread during the first index scan and gives it a moment."""

    def __init__(self, inner: IEventStore, during_scan: Callable[[], None]) -> None:
        self._inner = inner
        self._during_scan = during_scan
        self.fired = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def list_streams(self, prefix: str = "") -> list[str]:
        if not self.fired:
            self.fired = True
            self._during_scan()
        return self._inner.list_streams(prefix)


class TestTheKeyIndex:
    """A revocation used to miss its key while the index was being built, or after it went stale."""

    def test_a_revocation_during_the_index_build_finds_its_key(self) -> None:
        inner = InMemoryEventStore()
        creator = _key_store(inner)
        raw_keys = [creator.create_key(PRINCIPAL, f"key-{i}") for i in range(2)]
        ids = {meta.name: meta.key_id for meta in creator.list_keys(PRINCIPAL)}
        results: dict[str, bool] = {}
        second = threading.Thread(target=lambda: results.__setitem__("second", store.revoke_key(ids["key-1"])))

        def during_scan() -> None:
            # The second revocation arrives while the first is building the
            # index. The fixed store makes it wait for the scan, so this gives
            # it only a moment instead of waiting for it to finish.
            second.start()
            second.join(timeout=0.2)

        store = _key_store(_PauseTheFirstScan(inner, during_scan))
        results["first"] = store.revoke_key(ids["key-0"])
        second.join(timeout=30)

        assert results == {"first": True, "second": True}
        fresh = _key_store(inner)
        for raw_key in raw_keys:
            with pytest.raises(RevokedCredentialsError):
                fresh.get_principal_for_key(_key_hash(raw_key))

    def test_a_key_created_through_another_replica_can_be_revoked_here(self) -> None:
        inner = InMemoryEventStore()
        here = _key_store(inner)
        assert here.list_keys(PRINCIPAL) == []  # this replica's index is built, and empty
        raw_key, key_id = _one_key(inner)  # created through another replica

        assert here.revoke_key(key_id) is True

        with pytest.raises(RevokedCredentialsError):
            _key_store(inner).get_principal_for_key(_key_hash(raw_key))


class TestManyWritersOnOnePrincipal:
    @EVERY_STORE
    def test_every_assignment_and_revocation_lands_and_replays(
        self, make_store: Callable[[Path], IEventStore], tmp_path: Path, fine_switching: None
    ) -> None:
        race_role_writes([make_store(tmp_path)])


class TestAKeyRevocationIsNotBouncedByARotation:
    @EVERY_STORE
    def test_a_revocation_lands_although_a_rotation_got_in_first(
        self, make_store: Callable[[Path], IEventStore], tmp_path: Path
    ) -> None:
        inner = make_store(tmp_path)
        raw_key, key_id = _one_key(inner)
        other = _key_store(inner)
        store = _key_store(_LetAnotherWriterIn(inner, _key_stream(raw_key), lambda: other.rotate_key(key_id)))

        assert store.revoke_key(key_id) is True

        for view in (store, _key_store(inner)):
            with pytest.raises(RevokedCredentialsError):
                view.get_principal_for_key(_key_hash(raw_key))

    @EVERY_STORE
    def test_a_rotation_that_lost_to_a_revocation_is_refused_and_stores_no_new_key(
        self, make_store: Callable[[Path], IEventStore], tmp_path: Path
    ) -> None:
        inner = make_store(tmp_path)
        raw_key, key_id = _one_key(inner)
        other = _key_store(inner)
        store = _key_store(_LetAnotherWriterIn(inner, _key_stream(raw_key), lambda: other.revoke_key(key_id)))

        with pytest.raises(ValueError, match="revoked"):
            store.rotate_key(key_id)

        fresh = _key_store(inner)
        assert [meta.key_id for meta in fresh.list_keys(PRINCIPAL)] == [key_id]
        with pytest.raises(RevokedCredentialsError):
            fresh.get_principal_for_key(_key_hash(raw_key))

    def test_a_key_that_never_settles_is_reported_and_left_as_it_was(self) -> None:
        inner = InMemoryEventStore()
        raw_key, key_id = _one_key(inner)
        moving = _AlwaysMovedOn(inner, _key_stream(raw_key))

        with pytest.raises(ConcurrencyError):
            _key_store(moving).revoke_key(key_id)

        assert moving.attempts > 1, "a conflict is decided again on a fresh load before it is reported"
        assert _key_store(inner).get_principal_for_key(_key_hash(raw_key)) is not None
        assert not any(isinstance(e, ApiKeyRevoked) for e in inner.read_stream(_key_stream(raw_key)))

    @EVERY_STORE
    def test_many_revocations_and_rotations_all_land_or_are_refused(
        self, make_store: Callable[[Path], IEventStore], tmp_path: Path, fine_switching: None
    ) -> None:
        race_key_writes([make_store(tmp_path)])


def _fill_to_two_short_of_a_snapshot(store: EventSourcedRoleStore) -> None:
    """Write filler events until two more appends make the stream take a snapshot."""
    for i in range(EventSourcedRoleStore.SNAPSHOT_INTERVAL - 2):
        (store.assign_role if i % 2 == 0 else store.revoke_role)(PRINCIPAL, "filler")


def revoke_a_role_across_a_race_and_a_restart(open_store: Callable[[], IEventStore], *, cross_a_snapshot: bool) -> bool:
    """A revocation races a grant on the same principal, and then the process restarts.

    `open_store` opens the durable store, and calling it again is the restart.
    Returns whether the revocation was told it succeeded. Whatever it was
    told, this process's view before the restart and a new process's replay
    after it both agree with it. A revocation that failed is in force nowhere,
    so there is nothing that could come back after a restart.

    With `cross_a_snapshot`, the racing save is the one that takes a snapshot.
    That snapshot is the only state a save leaves in memory, and it was built
    from an aggregate that had not seen the concurrent grant. The restart drops
    it, and the new process replays the log instead.
    """
    inner = open_store()
    other = _role_store(inner)
    if cross_a_snapshot:
        _fill_to_two_short_of_a_snapshot(other)
    other.assign_role(PRINCIPAL, "ops-admin")
    store = _role_store(_LetAnotherWriterIn(inner, ROLE_STREAM, lambda: other.assign_role(PRINCIPAL, "writer")))

    try:
        store.revoke_role(PRINCIPAL, "ops-admin")
        told_revoked = True
    except ConcurrencyError:
        told_revoked = False

    if told_revoked and cross_a_snapshot:
        assert PRINCIPAL in store._snapshot_store, "the save did not snapshot, so this proves nothing"
    expected = {"writer"} if told_revoked else {"ops-admin", "writer"}
    assert _roles(store) == expected, "this process, before the restart"
    assert _replayed_roles(open_store()) == expected, "a new process, after the restart"
    return told_revoked


def a_revocation_that_won_across_a_stale_snapshot_and_a_restart(open_store: Callable[[], IEventStore]) -> None:
    """Another writer's revocation wins; this process's racing save snapshots a state that predates it.

    The revocation is in force before the restart, from the snapshot plus the
    events after it, and after the restart, from the log alone.
    """
    inner = open_store()
    other = _role_store(inner)
    _fill_to_two_short_of_a_snapshot(other)
    other.assign_role(PRINCIPAL, "ops-admin")
    store = _role_store(
        _LetAnotherWriterIn(
            inner,
            ROLE_STREAM,
            lambda: other.revoke_role(PRINCIPAL, "ops-admin"),
            methods=("get_stream_version", "append", "append_at_end"),
        )
    )

    store.assign_role(PRINCIPAL, "writer")

    assert PRINCIPAL in store._snapshot_store, "the save did not snapshot, so this proves nothing"
    assert _roles(store) == {"writer"}, "this process, before the restart"
    assert _replayed_roles(open_store()) == {"writer"}, "a new process, after the restart"


def revoke_a_key_across_a_race_and_a_restart(open_store: Callable[[], IEventStore]) -> bool:
    """A key revocation races a rotation of the same key, and then the process restarts.

    Returns whether the revocation was told it succeeded. Whatever it was
    told, authentication in this process before the restart and in a new
    process after it both agree with it.
    """
    inner = open_store()
    raw_key, key_id = _one_key(inner)
    other = _key_store(inner)
    store = _key_store(_LetAnotherWriterIn(inner, _key_stream(raw_key), lambda: other.rotate_key(key_id)))

    try:
        told_revoked = store.revoke_key(key_id)
    except ConcurrencyError:
        told_revoked = False

    for view, when in ((store, "before the restart"), (_key_store(open_store()), "after the restart")):
        if told_revoked:
            with pytest.raises(RevokedCredentialsError):
                view.get_principal_for_key(_key_hash(raw_key))
        else:
            assert view.get_principal_for_key(_key_hash(raw_key)) is not None, when
    return told_revoked


@pytest.fixture
def sqlite_file(tmp_path: Path) -> Callable[[], IEventStore]:
    """Opens the same SQLite file on every call; calling it again is a restart."""
    return lambda: SQLiteEventStore(tmp_path / "events.db")


SNAPSHOT_OR_NOT = pytest.mark.parametrize("cross_a_snapshot", [False, True], ids=["no-snapshot", "crossing-a-snapshot"])


class TestARestartReplaysWhatCallersWereTold:
    """Can a revocation lost to the race come back after a restart? Checked against a durable store.

    The `holds` tests pass before the fix as well. A revocation that lost the
    race then failed with `ConcurrencyError`, and it was in force neither in
    memory nor in the log, so a restart had nothing to bring back. The `is in
    force` tests are the fix: the revocation lands, and stays landed across the
    restart.
    """

    @SNAPSHOT_OR_NOT
    def test_a_raced_role_revocation_is_in_force_before_and_after_a_restart(
        self, sqlite_file: Callable[[], IEventStore], cross_a_snapshot: bool
    ) -> None:
        assert revoke_a_role_across_a_race_and_a_restart(sqlite_file, cross_a_snapshot=cross_a_snapshot) is True

    @SNAPSHOT_OR_NOT
    def test_what_a_raced_role_revocation_was_told_holds_before_and_after_a_restart(
        self, sqlite_file: Callable[[], IEventStore], cross_a_snapshot: bool
    ) -> None:
        revoke_a_role_across_a_race_and_a_restart(sqlite_file, cross_a_snapshot=cross_a_snapshot)

    def test_a_role_revocation_that_won_is_not_undone_by_a_stale_snapshot_or_a_restart(
        self, sqlite_file: Callable[[], IEventStore]
    ) -> None:
        a_revocation_that_won_across_a_stale_snapshot_and_a_restart(sqlite_file)

    def test_a_raced_key_revocation_is_in_force_before_and_after_a_restart(
        self, sqlite_file: Callable[[], IEventStore]
    ) -> None:
        assert revoke_a_key_across_a_race_and_a_restart(sqlite_file) is True

    def test_what_a_raced_key_revocation_was_told_holds_before_and_after_a_restart(
        self, sqlite_file: Callable[[], IEventStore]
    ) -> None:
        revoke_a_key_across_a_race_and_a_restart(sqlite_file)
