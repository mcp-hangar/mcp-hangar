"""A grant removed from ``auth.role_assignments`` is revoked at the next boot (#2052).

Every boot applied the file's entries with ``assign_role`` and nothing ever took
one away, so on a durable store a line deleted from config.yaml kept its role
for good. The file's grants are now recorded against `CONFIG_ROLE_ASSIGNER`,
and a boot revokes the ones the file no longer lists. Grants anyone else made
are left alone, and so are untracked grants written before the tag existed:
those are named in a warning instead.

Each scenario takes ``open_store``, which returns the store a fresh process
would open: a new handle on the same SQLite file or event store. For the
in-memory store a restart loses everything anyway, so it returns the same
instance and the scenario checks the reconciliation itself. The same scenarios
run on PostgreSQL in ``tests/integration/test_config_role_grants_on_one_postgres.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from structlog.testing import capture_logs

from mcp_hangar.auth.bootstrap import apply_config_role_assignments, bootstrap_auth
from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig
from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore
from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteRoleStore
from mcp_hangar.domain.contracts.authorization import CONFIG_ROLE_ASSIGNER, IRoleStore, NullRoleStore
from mcp_hangar.domain.model.event_sourced_role_assignment import (
    EventSourcedRoleAssignment,
    RoleAssignmentSnapshot,
)
from mcp_hangar.domain.value_objects import PrincipalId
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore

OpenStore = Callable[[], IRoleStore]

ANN = "service:ann"
BOB = "group:bob-admins"


def _entry(principal: str, role: str, scope: str = "global") -> RoleAssignment:
    return RoleAssignment(principal=principal, role=role, scope=scope)


def _roles(store: IRoleStore, principal: str) -> set[str]:
    return {r.name for r in store.get_roles_for_principal(principal)}


def _assigner(store: IRoleStore, principal: str, role: str) -> str | None:
    rows = [r for r in store.list_role_assignments() if (r["principal_id"], r["role_name"]) == (principal, role)]
    assert len(rows) == 1, rows
    return rows[0]["assigned_by"]


# --- scenarios, shared with the PostgreSQL integration test -------------------


def an_entry_removed_from_the_file_is_revoked(open_store: OpenStore) -> None:
    apply_config_role_assignments(open_store(), [_entry(ANN, "viewer"), _entry(BOB, "admin")])

    store = open_store()
    apply_config_role_assignments(store, [_entry(ANN, "viewer")])

    assert _roles(store, BOB) == set()
    assert _roles(store, ANN) == {"viewer"}
    assert _roles(open_store(), BOB) == set()


def a_scoped_entry_is_revoked_only_in_its_scope(open_store: OpenStore) -> None:
    apply_config_role_assignments(open_store(), [_entry(ANN, "developer", "tenant:a"), _entry(ANN, "viewer")])

    store = open_store()
    apply_config_role_assignments(store, [_entry(ANN, "viewer")])

    assert [(r["role_name"], r["scope"]) for r in store.list_role_assignments()] == [("viewer", "global")]


def an_api_grant_survives_a_boot_whose_file_never_had_it(open_store: OpenStore) -> None:
    store = open_store()
    apply_config_role_assignments(store, [])
    store.assign_role(ANN, "admin", assigned_by="user:operator")
    store.assign_role(BOB, "admin", assigned_by="local-cli-bootstrap")

    store = open_store()
    apply_config_role_assignments(store, [])

    assert _roles(store, ANN) == {"admin"}
    assert _roles(store, BOB) == {"admin"}


def an_untracked_grant_not_in_the_file_is_kept_and_named(open_store: OpenStore) -> None:
    # Written the way every store wrote grants before the tag: no assigner.
    open_store().assign_role(ANN, "admin", scope="tenant:a")

    store = open_store()
    with capture_logs() as logs:
        apply_config_role_assignments(store, [_entry(BOB, "viewer")])

    assert _roles(store, ANN) == {"admin"}
    warnings = [e for e in logs if e["event"] == "untracked_role_assignment_not_in_config"]
    assert len(warnings) == 1
    assert (warnings[0]["principal"], warnings[0]["role"], warnings[0]["scope"]) == (ANN, "admin", "tenant:a")
    assert "DELETE /api/auth/roles/revoke" in warnings[0]["detail"]
    assert warnings[0]["log_level"] == "warning"


def an_untracked_grant_the_file_lists_is_adopted_by_it(open_store: OpenStore) -> None:
    open_store().assign_role(ANN, "admin")

    store = open_store()
    apply_config_role_assignments(store, [_entry(ANN, "admin")])
    assert _assigner(store, ANN, "admin") == CONFIG_ROLE_ASSIGNER

    store = open_store()
    apply_config_role_assignments(store, [])
    assert _roles(store, ANN) == set()


def a_grant_the_api_made_first_stays_when_the_file_drops_it(open_store: OpenStore) -> None:
    store = open_store()
    store.assign_role(ANN, "admin", assigned_by="user:operator")
    apply_config_role_assignments(store, [_entry(ANN, "admin")])
    assert _assigner(store, ANN, "admin") == "user:operator"

    store = open_store()
    apply_config_role_assignments(store, [])
    assert _roles(store, ANN) == {"admin"}


def a_grant_the_api_repeats_after_the_file_stays_when_the_file_drops_it(open_store: OpenStore) -> None:
    store = open_store()
    apply_config_role_assignments(store, [_entry(ANN, "admin")])
    store.assign_role(ANN, "admin", assigned_by="user:operator")
    assert _assigner(store, ANN, "admin") == "user:operator"

    store = open_store()
    apply_config_role_assignments(store, [])
    assert _roles(store, ANN) == {"admin"}


def a_line_still_in_the_file_keeps_its_grant_even_if_reapplying_it_fails(open_store: OpenStore) -> None:
    apply_config_role_assignments(open_store(), [_entry(ANN, "viewer")])
    store = open_store()
    original = store.assign_role

    def failing(*args, **kwargs):
        raise ValueError("unavailable")

    store.assign_role = failing  # type: ignore[method-assign]
    try:
        apply_config_role_assignments(store, [_entry(ANN, "viewer")])
    finally:
        store.assign_role = original  # type: ignore[method-assign]
    assert _roles(store, ANN) == {"viewer"}


SCENARIOS = [
    an_entry_removed_from_the_file_is_revoked,
    a_scoped_entry_is_revoked_only_in_its_scope,
    an_api_grant_survives_a_boot_whose_file_never_had_it,
    an_untracked_grant_not_in_the_file_is_kept_and_named,
    an_untracked_grant_the_file_lists_is_adopted_by_it,
    a_grant_the_api_made_first_stays_when_the_file_drops_it,
    a_grant_the_api_repeats_after_the_file_stays_when_the_file_drops_it,
    a_line_still_in_the_file_keeps_its_grant_even_if_reapplying_it_fails,
]


# --- stores -------------------------------------------------------------------


@pytest.fixture(params=["memory", "sqlite", "event_sourced"])
def open_store(request, tmp_path) -> Iterator[OpenStore]:
    if request.param == "memory":
        memory = InMemoryRoleStore()
        yield lambda: memory
    elif request.param == "sqlite":
        opened: list[SQLiteRoleStore] = []

        def open_sqlite() -> IRoleStore:
            s = SQLiteRoleStore(db_path=str(tmp_path / "auth.db"))
            s.initialize()
            opened.append(s)
            return s

        yield open_sqlite
        for s in opened:
            s.close()
    else:
        events = InMemoryEventStore()
        yield lambda: EventSourcedRoleStore(event_store=events)


@pytest.mark.security
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__)
def test_config_role_assignments_are_reconciled(open_store: OpenStore, scenario) -> None:
    scenario(open_store)


# --- through bootstrap_auth, as a restart runs it ------------------------------


def _config(storage: StorageConfig, entries: list[RoleAssignment]) -> AuthConfig:
    return AuthConfig(enabled=True, storage=storage, api_key=ApiKeyAuthConfig(enabled=False), role_assignments=entries)


@pytest.mark.security
def test_a_restart_on_sqlite_revokes_a_line_removed_from_the_file(tmp_path) -> None:
    storage = StorageConfig(driver="sqlite", path=str(tmp_path / "auth.db"))
    bootstrap_auth(_config(storage, [_entry(BOB, "admin"), _entry(ANN, "viewer")]))

    after = bootstrap_auth(_config(storage, [_entry(ANN, "viewer")]))

    assert _roles(after.role_store, BOB) == set()
    assert _roles(after.role_store, ANN) == {"viewer"}


@pytest.mark.security
def test_a_restart_on_the_event_store_revokes_a_line_removed_from_the_file() -> None:
    events = InMemoryEventStore()
    storage = StorageConfig(driver="event_sourcing")
    bootstrap_auth(_config(storage, [_entry(BOB, "admin")]), event_store=events)

    after = bootstrap_auth(_config(storage, []), event_store=events)

    assert _roles(after.role_store, BOB) == set()


# --- the pieces ---------------------------------------------------------------


def test_no_authenticated_caller_can_be_recorded_as_the_config_file() -> None:
    with pytest.raises(ValueError):
        PrincipalId(CONFIG_ROLE_ASSIGNER)


def test_a_snapshot_keeps_who_each_grant_is_recorded_against() -> None:
    agg = EventSourcedRoleAssignment("service:ann")
    agg.assign_role("admin", "global", CONFIG_ROLE_ASSIGNER)
    agg.assign_role("viewer", "tenant:a", "user:operator")

    snapshot = RoleAssignmentSnapshot.from_dict(agg.create_snapshot().to_dict())
    restored = EventSourcedRoleAssignment.from_snapshot(snapshot)

    assert sorted(restored.list_assignments()) == [
        ("global", "admin", CONFIG_ROLE_ASSIGNER),
        ("tenant:a", "viewer", "user:operator"),
    ]


def test_a_snapshot_written_before_the_tag_reads_as_untracked() -> None:
    snapshot = RoleAssignmentSnapshot.from_dict(
        {"principal_id": "service:ann", "assignments": {"global": ["admin"]}, "version": 3}
    )

    assert EventSourcedRoleAssignment.from_snapshot(snapshot).list_assignments() == [("global", "admin", None)]


def test_the_null_store_lists_no_assignments() -> None:
    assert NullRoleStore().list_role_assignments() == []


def test_a_racing_config_assignment_does_not_take_over_an_api_grant_on_replay() -> None:
    # Two replicas that both loaded the principal without the role each append one.
    from mcp_hangar.domain.events import RoleAssigned

    events = [
        RoleAssigned(principal_id=ANN, role_name="admin", scope="global", assigned_by="user:operator"),
        RoleAssigned(principal_id=ANN, role_name="admin", scope="global", assigned_by=CONFIG_ROLE_ASSIGNER),
    ]

    assert EventSourcedRoleAssignment.from_events(ANN, events).list_assignments() == [
        ("global", "admin", "user:operator")
    ]


# --- the bootstrap admin owns its grant ---------------------------------------


def the_bootstrap_admin_keeps_a_grant_the_file_also_made(role_store: IRoleStore, key_store) -> None:
    """``bootstrap-admin`` for a principal the file lists, then the line removed: still admin."""
    apply_config_role_assignments(role_store, [_entry(ANN, "admin"), _entry(BOB, "admin")])
    assert key_store.bootstrap_initial_admin(ANN, "first-admin") is not None
    assert _assigner(role_store, ANN, "admin") == "local-cli-bootstrap"

    apply_config_role_assignments(role_store, [])

    assert _roles(role_store, ANN) == {"admin"}
    assert _roles(role_store, BOB) == set()


def the_bootstrap_admin_takes_over_an_untracked_grant(role_store: IRoleStore, key_store) -> None:
    role_store.assign_role(ANN, "admin")
    assert key_store.bootstrap_initial_admin(ANN, "first-admin") is not None

    with capture_logs() as logs:
        apply_config_role_assignments(role_store, [])

    assert _assigner(role_store, ANN, "admin") == "local-cli-bootstrap"
    assert _roles(role_store, ANN) == {"admin"}
    assert not [e for e in logs if e["event"] == "untracked_role_assignment_not_in_config"]


BOOTSTRAP_SCENARIOS = [
    the_bootstrap_admin_keeps_a_grant_the_file_also_made,
    the_bootstrap_admin_takes_over_an_untracked_grant,
]


@pytest.mark.security
@pytest.mark.parametrize("scenario", BOOTSTRAP_SCENARIOS, ids=lambda f: f.__name__)
def test_the_bootstrap_admin_owns_its_grant_on_sqlite(tmp_path, scenario) -> None:
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    db = str(tmp_path / "auth.db")
    role_store = SQLiteRoleStore(db_path=db)
    role_store.initialize()
    key_store = SQLiteApiKeyStore(db)
    key_store.initialize()
    try:
        scenario(role_store, key_store)
    finally:
        role_store.close()
        key_store.close()
